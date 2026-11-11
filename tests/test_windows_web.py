import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flowhmr import model_files as files, model_integrity as integrity, model_sources as sources
from flowhmr.model_downloads import missing_downloads


_spec = spec_from_file_location("flowhmr_windows_web", Path(__file__).resolve().parents[1] / "tools/web/server.py")
server = module_from_spec(_spec)
_spec.loader.exec_module(server)


class HistoryTest(unittest.TestCase):
    @staticmethod
    def row(index, **fields):
        return dict(id=f"20261111_120000_{index:06x}", created=1700000000 + index // 2,
                    status="done", progress=100, filename=f"{index}.mp4", finished=1700000200,
                    params={}, **fields)

    def test_paginates_old_and_multiplayer_jobs_and_refreshes_across_deletion(self):
        rows = [self.row(i) for i in range(57)]
        rows[0].update(status="running", progress=9)
        rows[6]["person_batch"] = rows[6]["id"]
        rows[5]["person_batch"] = rows[6]["id"]
        jobs = {j["id"]: j for j in rows}
        with patch.object(server, "JOBS", jobs), server.app.test_client() as client:
            first = client.get("/api/jobs").get_json()
            self.assertEqual(first["total"], 56)
            self.assertEqual(first["deletable_count"], 55)
            self.assertEqual(len(first["jobs"]), 21)
            self.assertEqual(first["jobs"][-1]["id"], rows[0]["id"])  # Old active work is pinned.

            second = client.get("/api/jobs", query_string={"before": first["next_cursor"]}).get_json()
            self.assertEqual(len(second["jobs"]), 20)
            boundary = second["cursor"]
            # A deletion of the cursor itself and a new job must not create gaps.
            jobs.pop(second["jobs"][-1]["id"])
            newest = self.row(57)
            jobs[newest["id"]] = newest
            refreshed = client.get("/api/jobs", query_string={"through": boundary}).get_json()
            self.assertEqual(refreshed["jobs"][0]["id"], newest["id"])
            self.assertEqual(len(refreshed["jobs"]), 41)
            third = client.get("/api/jobs", query_string={"before": refreshed["next_cursor"]}).get_json()
            self.assertIsNone(third["next_cursor"])
            visible = {row["id"]: row for row in [*refreshed["jobs"], *third["jobs"]]}
            self.assertEqual(set(visible), set(jobs) - {rows[5]["id"]})
            self.assertEqual(visible[rows[6]["id"]]["person_count"], 2)
            bad = client.get("/api/jobs?before=not-a-page")
            self.assertEqual((bad.status_code, bad.get_json()["error_code"]), (400, "history_invalid_page"))


class ModelIntegrityTest(unittest.TestCase):
    def test_download_all_and_individual_actions_only_offer_public_files(self):
        with TemporaryDirectory() as temporary, patch.object(files, "MODEL_ROOT", Path(temporary)):
            args = SimpleNamespace(device="cuda:0", ckpt=None, model_cfg=None)
            with (patch.object(server, "ARGS", args), patch.object(server.RUNTIME, "summary", return_value={"pending_restart": False}),
                  patch.object(server.MODEL_DOWNLOADS, "start", return_value={"status": "idle"}) as start,
                  server.app.test_client() as client):
                self.assertEqual(client.post("/api/models/download", json={}).status_code, 200)
                core = start.call_args.args[0]
                self.assertEqual(set(core), set(sources.PUBLIC_ASSETS))
                self.assertEqual(client.post("/api/models/download", json={"group": "hamer"}).status_code, 200)
                optional = start.call_args.args[0]
                self.assertEqual(set(optional), {"hamer"})
                self.assertTrue(set(sources.HAMER_DIRECT_ASSETS) <= set(optional["hamer"]))
                self.assertNotIn(sources.HAMER_ARCHIVE, optional["hamer"])
                self.assertEqual(client.post("/api/models/download", json={"group": "vggt"}).status_code, 200)
                self.assertEqual(set(start.call_args.args[0]), {"vggt"})
                self.assertEqual(client.post("/api/models/download", json={"group": "all"}).status_code, 200)
                self.assertEqual(set(start.call_args.args[0]), set(core) | {"hamer"})
                for name in ("mano", "smplh", "not-a-model"):
                    self.assertEqual(client.post("/api/models/download", json={"group": name}).status_code, 400)

    def test_neutral_is_optional_if_one_body_reference_is_installed(self):
        with (TemporaryDirectory() as temporary, patch.object(files, "MODEL_ROOT", Path(temporary)),
              patch.object(files, "smplh_reference", return_value="female") as reference):
            items = {item["id"]: item for item in server.model_inventory("cpu")["items"]}
            self.assertEqual((items["smplh"]["required"], items["smplh"]["blocking"]), (False, False))
            self.assertFalse(items["mano"]["blocking"])
            self.assertEqual(items["mano"]["issue_code"], "model_mano_partial")
            self.assertEqual(files.core_model_paths()[-2], files.resolve_model(files.SMPLH_FILES["female"]))
            reference.return_value = None
            missing = {item["id"]: item for item in server.model_inventory("cpu")["items"]}
            self.assertTrue(missing["mano"]["blocking"])
            self.assertFalse(missing["smplh"]["blocking"])

    def test_verification_uses_active_weights_and_preserves_edited_config(self):
        with TemporaryDirectory() as temporary:
            models = Path(temporary)
            config, weight = (models / relative for relative in (files.FLOWHMR_CONFIG, files.FLOWHMR_CKPT))
            config.parent.mkdir()
            config.write_text("motion_fps: 30  # my config\n", encoding="utf-8")
            weight.write_bytes(b"broken model")
            expected = b"actual model"
            asset = sources.Asset(files.FLOWHMR_CKPT, len(expected), hashlib.sha256(expected).hexdigest())
            yaml_asset = sources.Asset(files.FLOWHMR_CONFIG, 1, "0" * 64)
            cache = models / "cache.json"
            cache.write_text(json.dumps({"version": integrity._CACHE_VERSION, "files": []}), encoding="utf-8")
            with (patch.object(files, "MODEL_ROOT", models), patch.object(integrity, "_CACHE_PATH", cache),
                  patch.object(integrity, "_cache", None), patch.object(integrity, "_cache_stamp", None),
                  patch.object(integrity, "_assets", {asset.relative: asset, yaml_asset.relative: yaml_asset}),
                  patch.object(sources, "PUBLIC_ASSETS", {"flowhmr": (asset, yaml_asset)})):
                self.assertTrue(files.file_present(config))
                self.assertTrue(files.file_present(weight))
                report = integrity.summary(deep=True, required_paths=(config, weight))
                self.assertEqual(report["checked"], 2)
                self.assertEqual([item["detail_code"] for item in report["problems"]], ["file_digest_mismatch"])
                self.assertEqual(missing_downloads("cpu"), {"flowhmr": (asset,)})
                weight.write_bytes(expected)
                self.assertEqual(integrity.summary(deep=True, required_paths=(config, weight))["problems"], [])

    def test_windows_asset_case_uses_published_size(self):
        if sys.platform != "win32":
            self.skipTest("Windows path behavior")
        with TemporaryDirectory() as temporary, patch.object(files, "MODEL_ROOT", Path(temporary)):
            path = Path(temporary) / files.JOINT_REGRESSOR.upper()
            path.parent.mkdir()
            path.write_bytes(b"truncated")
            result = integrity.check(path)
            self.assertEqual(result.state, "incomplete")
            self.assertEqual(result.relative, files.JOINT_REGRESSOR)


class StaticModuleTest(unittest.TestCase):
    def test_browser_revalidates_module_with_explicit_javascript_mime(self):
        with server.app.test_client() as client:
            first = client.get("/static/js/api.js")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.mimetype, "text/javascript")
            # An old client may have cached the identical JS bytes as text/plain.
            refreshed = client.get("/static/js/api.js", headers={"If-None-Match": first.headers["ETag"]})
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(refreshed.mimetype, "text/javascript")


if __name__ == "__main__":
    unittest.main()
