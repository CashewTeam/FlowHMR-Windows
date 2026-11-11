import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
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


class VideoSourceTest(unittest.TestCase):
    def test_partial_preview_and_original_keep_separate_byte_ranges(self):
        with TemporaryDirectory() as directory:
            job_id = "20261111_120000_000001"
            job_dir = Path(directory, job_id)
            canonical = job_dir / "input/input_30fps.mp4"
            canonical.parent.mkdir(parents=True)
            original = b"0123456789original"
            converted = b"abpreview"
            (job_dir / "input.mp4").write_bytes(original)
            job = dict(id=job_id, ext=".mp4", status="running", source_frames=60, created=0)
            with (patch.object(server, "ARGS", SimpleNamespace(work_dir=directory)),
                  patch.object(server, "JOBS", {job_id: job}),
                  patch.object(server, "MEMBER_DIRS", {}), server.app.test_client() as client):
                status = f"/api/jobs/{job_id}"
                raw = f"{status}/video?source=original"
                preview = f"{status}/video?source=preview"
                self.assertFalse(client.get(status).json["has_preview_video"])
                self.assertEqual(client.get(preview).status_code, 404)
                with client.get(raw, headers={"Range": "bytes=0-1"}) as head:
                    self.assertEqual((head.status_code, head.data), (206, original[:2]))
                canonical.write_bytes(converted)
                self.assertTrue(client.get(status).json["has_preview_video"])
                with client.get(raw, headers={"Range": "bytes=10-"}) as tail:
                    self.assertEqual((tail.status_code, tail.data), (206, original[10:]))
                    self.assertEqual(tail.headers["Content-Range"], f"bytes 10-{len(original)-1}/{len(original)}")
                with client.get(preview, headers={"Range": "bytes=2-"}) as tail:
                    self.assertEqual((tail.status_code, tail.data), (206, converted[2:]))
                    self.assertEqual(tail.headers["Content-Range"], f"bytes 2-{len(converted)-1}/{len(converted)}")


class CharacterFileTest(unittest.TestCase):
    def test_native_pick_exposes_only_model_and_local_textures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "角色/奥黛塔.pmx"
            (model.parent / "tex").mkdir(parents=True)
            model.write_bytes(b"PMX")
            (model.parent / "tex/皮肤.png").write_bytes(b"texture")
            (model.parent / "other.fbx").write_bytes(b"unselected")
            (root / "private.png").write_bytes(b"outside")
            with (patch.dict(os.environ, {server.CHARACTER_KEY_ENV: "test-key"}),
                  patch.object(server, "CHARACTER_FILES", server.CharacterFiles()), server.app.test_client() as client):
                register = "/api/desktop/character-file"
                self.assertEqual(client.post(register, json={"file": str(model)}).status_code, 403)
                auth = {"X-FlowHMR-Desktop-Key": "test-key"}
                self.assertEqual(client.post(register, json={"file": str(model)},
                                             headers={**auth, "Origin": "https://example.org"}).status_code, 403)
                choice = client.post(register, json={"file": str(model)}, headers=auth).json
                self.assertEqual(choice["name"], model.name)
                path = f"/api/character-resource/{choice['ticket']}"
                with client.get(path) as picked:
                    self.assertEqual(picked.data, b"PMX")
                    self.assertEqual(picked.headers["Cache-Control"], "no-store")
                with client.get(path, query_string={"texture": r"tex\皮肤.png"}) as texture:
                    self.assertEqual(texture.data, b"texture")
                for invalid in ("../private.png", str(root / "private.png"), "other.fbx", "https://example.org/a.png"):
                    self.assertEqual(client.get(path, query_string={"texture": invalid}).status_code, 404)
                self.assertEqual(client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_character_library_reopens_copies_and_keeps_each_actors_pose(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original"
            texture = source / "textures/皮肤.png"
            texture.parent.mkdir(parents=True)
            texture.write_bytes(b"referenced image")
            (source / "unrelated.png").write_bytes(b"not used")
            pmx, fbx = source / "奥黛塔.pmx", source / "body.fbx"
            pmx.write_bytes(b"model one")
            fbx.write_bytes(b"model two")
            library_dir = root / "models/characters"
            path = "/api/character-library"
            jobs = {f"actor{i}": {"status": "done", "selected_person": i - 1} for i in (1, 2)}
            with (patch.dict(os.environ, {server.CHARACTER_KEY_ENV: "test-key"}),
                  patch.object(server, "JOBS", jobs), server.app.test_client() as client):
                def save(file, image=None):
                    choice = client.post("/api/desktop/character-file", json={"file": str(file)},
                                         headers={"X-FlowHMR-Desktop-Key": "test-key"}).get_json()
                    ticket = choice["ticket"]
                    if image:
                        with client.get(f"/api/character-resource/{ticket}", query_string={"texture": image}) as response:
                            self.assertEqual(response.data, b"referenced image")
                    saved = client.post(path, json={"ticket": ticket, "name": file.stem})
                    self.assertEqual(saved.status_code, 200, saved.get_json())
                    return saved.json["id"]

                def assign(person, identifier, pose=None, select=True):
                    res = client.put(f"{path}/actors/actor{person}",
                                     json=dict(id=identifier, person=person, offsets=pose, select=select))
                    self.assertEqual(res.status_code, 200, res.get_json())

                with (patch.object(server, "CHARACTER_FILES", server.CharacterFiles()),
                      patch.object(server, "CHARACTER_LIBRARY", server.CharacterLibrary(library_dir))):
                    first = save(pmx, r"textures\皮肤.png")
                    second = save(fbx)
                    self.assertEqual(first, save(pmx, "textures/皮肤.png"))
                    self.assertFalse((library_dir / first / "files/unrelated.png").exists())
                    assign(1, first)
                    assign(2, first)
                    assign(1, first, {"rootTranslation": [0.32, 0, 0]}, select=False)
                    assign(2, first, {"rotations": {"15": [22, 0, 0]}}, select=False)
                    assign(2, second)
                    self.assertEqual(client.get(path, headers={"Origin": "https://example.org"}).status_code, 403)

                texture.unlink()
                pmx.unlink()
                # Fresh objects and a different loopback origin stand in for a new app process.
                with (patch.object(server, "CHARACTER_FILES", server.CharacterFiles()),
                      patch.object(server, "CHARACTER_LIBRARY", server.CharacterLibrary(library_dir))):
                    state = client.get(path, query_string=[("actor", "actor1"), ("actor", "actor2")],
                                       base_url="http://127.0.0.1:42561").get_json()
                    self.assertEqual({item["id"] for item in state["models"]}, {first, second})
                    one, two = (state["actors"][f"actor{i}"] for i in (1, 2))
                    self.assertEqual((one["id"], two["id"]), (first, second))
                    self.assertEqual(one["poses"][first]["rootTranslation"], [0.32, 0, 0])
                    self.assertEqual(two["poses"][first]["rotations"]["15"], [22, 0, 0])
                    self.assertEqual([state["defaults"][str(i)]["id"] for i in (1, 2)], [first, second])
                    choice = client.post(f"{path}/{first}/open").get_json()
                    resource = f"/api/character-resource/{choice['ticket']}"
                    with client.get(resource) as model, client.get(
                            resource, query_string={"texture": "textures/皮肤.png"}) as image:
                        self.assertEqual((model.data, image.data), (b"model one", b"referenced image"))
                    assign(1, None)
                    assign(1, first, {"rootTranslation": [1, 0, 0]}, select=False)
                    state = client.get(path, query_string=[("actor", "actor1"), ("actor", "actor2")]).get_json()
                    self.assertIsNone(state["actors"]["actor1"]["id"])
                    self.assertIsNone(state["defaults"]["1"]["id"])
                    self.assertEqual(state["actors"]["actor2"]["id"], second)

    def test_delete_character_removes_copy_and_assignments_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original"
            source.mkdir()
            model, texture, other = source / "角色.pmx", source / "材质.png", source / "other.fbx"
            for path, content in ((model, b"model"), (texture, b"texture"), (other, b"other")):
                path.write_bytes(content)
            files = server.CharacterFiles()
            target = root / "models/characters"
            library = server.CharacterLibrary(target)
            choice = files.register(str(model))
            self.assertEqual(files.resource(choice["ticket"], texture.name), texture.resolve())
            removed = library.save(files, choice["ticket"], "角色")["id"]
            kept = library.save(files, files.register(str(other))["ticket"], "Other")["id"]
            copy_ticket = library.open(removed, files)["ticket"]
            pose = {"rotations": {"13": [0, 0, 11], "14": [0, 0, -11]}, "mirror": True}
            library.set_actor("first", "1", removed, pose, select=True)
            library.set_actor("first", "1", removed, pose, select=False)
            library.set_actor("second", "2", removed, pose, select=True)
            library.set_actor("second", "2", kept, None, select=True)
            library.set_actor("third", "3", removed, pose, select=True)
            self.assertEqual((target / removed / "files" / texture.name).read_bytes(), b"texture")

            with (patch.object(server, "CHARACTER_FILES", files),
                  patch.object(server, "CHARACTER_LIBRARY", library), server.app.test_client() as client):
                url = f"/api/character-library/{removed}"
                self.assertEqual(client.delete(url, headers={"Origin": "https://example.org"}).status_code, 403)
                self.assertEqual(client.delete("/api/character-library/invalid").status_code, 404)
                self.assertTrue((target / removed).is_dir())
                reply = client.delete(url, query_string={"actor": ["first", "second", "third"]})
                self.assertEqual(reply.status_code, 200, reply.get_json())
                self.assertFalse(reply.json["cleanup_pending"])
                self.assertEqual(client.get(f"/api/character-resource/{copy_ticket}").status_code, 404)

            self.assertFalse((target / removed).exists())
            self.assertEqual(list(target.glob(".removed-*")), [])
            self.assertEqual((model.read_bytes(), texture.read_bytes(), other.read_bytes()), (b"model", b"texture", b"other"))
            restored = server.CharacterLibrary(target).snapshot(["first", "second", "third"])
            self.assertEqual([item["id"] for item in restored["models"]], [kept])
            self.assertIsNone(restored["actors"]["first"]["id"])
            self.assertIsNone(restored["actors"]["third"]["id"])
            self.assertEqual(restored["actors"]["second"]["id"], kept)
            self.assertNotIn(removed, restored["actors"]["second"]["poses"])
            self.assertNotIn(removed, restored["presets"])
            self.assertEqual(list(restored["defaults"]), ["2"])


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
    def test_archive_import_is_local_and_preserves_folder_clients(self):
        args = SimpleNamespace(device="cuda:0", ckpt=None, model_cfg=None)
        with (patch.object(server, "ARGS", args), patch.object(server, "JOBS", {}),
              patch.object(server.RUNTIME, "summary", return_value={"pending_restart": False}),
              patch.object(server.MODEL_IMPORTER, "start", return_value={"state": "running"}) as start,
              server.app.test_client() as client):
            request = {"path": "C:/Models/mano_v1_2.zip", "kind": "archive"}
            denied = client.post("/api/models/import", json=request, headers={"Origin": "https://example.org"})
            self.assertEqual(denied.status_code, 403)
            self.assertFalse(start.called)
            self.assertEqual(client.post("/api/models/import", json={**request, "kind": "other"}).status_code, 400)
            self.assertEqual(client.post("/api/models/import", json=request).status_code, 202)
            start.assert_called_once_with(request["path"], kind="archive")
            self.assertEqual(client.post("/api/models/import", json={"path": "C:/Models/mano_v1_2"}).status_code, 202)
            start.assert_called_with("C:/Models/mano_v1_2", kind="folder")

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
            self.assertEqual(files.core_model_paths()[-2], files.resolve_model(files.SMPLH_FILES["male"]))

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
