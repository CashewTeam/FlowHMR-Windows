import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from flowhmr import model_files as files, model_import, model_sources


class ModelImportTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(prefix="flowhmr-model-import-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source = self.root / "解压后的模型"
        self.source.mkdir()
        self.installer = model_import.ModelImporter(self.root / "models")
        self.relative = files.JOINT_REGRESSOR
        self.source_name = Path(self.relative).name
        self.destination = self.installer.root / self.relative
        self.content = b"known published model fixture"
        asset = model_sources.Asset(self.relative, len(self.content), hashlib.sha256(self.content).hexdigest())
        assets = patch.dict(model_import._ASSETS, {self.relative: asset})
        assets.start()
        self.addCleanup(assets.stop)

    def run_import(self, source=None):
        response = self.installer.start(str(source or self.source))
        self.assertEqual(response["state"], "running")
        deadline = monotonic() + 5
        while self.installer.active and monotonic() < deadline:
            sleep(.01)
        response = self.installer.status()
        self.assertIn(response["state"], {"completed", "failed"})
        return response

    def test_copy_from_unicode_folder_then_skip_without_overwrite(self):
        source = self.source / "archive" / self.source_name
        source.parent.mkdir()
        source.write_bytes(self.content)
        report = self.run_import()
        self.assertEqual(report["results"], [{"file": self.relative, "status": "installed"}])
        self.assertEqual(report["groups"], ["joint-regressor"])
        self.assertEqual(report["bytes_done"], len(self.content))
        self.assertEqual(source.read_bytes(), self.destination.read_bytes())

        before = self.destination.stat().st_mtime_ns
        report = self.run_import()
        self.assertEqual(report["results"][0]["code"], "already_installed")
        self.assertEqual(self.destination.stat().st_mtime_ns, before)
        self.assertEqual(source.read_bytes(), self.content)

    def test_checksum_failure_publishes_nothing(self):
        (self.source / self.source_name).write_bytes(b"!" * len(self.content))
        report = self.run_import()
        self.assertEqual(report["results"], [{"file": self.relative, "status": "invalid",
                                               "code": "checksum_mismatch"}])
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.installer.root.rglob("*.import.part")))

    def test_conflict_preserves_destination_and_cleans_stale_part(self):
        (self.source / self.source_name).write_bytes(self.content)
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"existing model")
        part = model_import._part_path(self.destination)
        part.write_bytes(b"leftover from a prior process")
        report = self.run_import()
        self.assertEqual(report["results"][0]["code"], "destination_exists")
        self.assertEqual(self.destination.read_bytes(), b"existing model")
        self.assertFalse(part.exists())

    def test_publish_does_not_overwrite_a_file_appearing_during_copy(self):
        (self.source / self.source_name).write_bytes(self.content)
        actual_publish = model_import._publish

        def race(part, destination):
            destination.write_bytes(b"written by another process")
            actual_publish(part, destination)

        with patch.object(model_import, "_publish", side_effect=race):
            report = self.run_import()
        self.assertEqual(report["results"][0]["code"], "destination_exists")
        self.assertEqual(self.destination.read_bytes(), b"written by another process")
        self.assertFalse(list(self.installer.root.rglob("*.import.part")))

    def test_two_equally_good_sources_are_not_mixed(self):
        for name in ("one", "two"):
            nested = self.source / name
            nested.mkdir()
            (nested / self.source_name).write_bytes(self.content)
        report = self.run_import()
        self.assertEqual(report["results"][0]["code"], "multiple_candidates")
        self.assertFalse(self.destination.exists())

    def test_other_process_lock_blocks_install(self):
        (self.source / self.source_name).write_bytes(self.content)
        with model_import._target_lock(self.installer.root):
            report = self.run_import()
        self.assertEqual(report["error_code"], "busy")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.run_import()["results"][0]["status"], "installed")

    def test_edited_config_is_accepted_and_missing_checkpoint_reported(self):
        directory = self.source / "flowhmr_latest"
        directory.mkdir()
        config = directory / "config.yml"
        config.write_text("network_module: flowhmr/network/hymotion_mmdit_for_v2m.HunyuanMotionMMDiT\n"
                          "network_module_args: {}\ntrain_pipeline_args: {}\n# User edits stay intact.\n",
                          encoding="utf-8")
        report = self.run_import(directory)
        outcomes = {item["file"]: item["status"] for item in report["results"]}
        self.assertEqual(outcomes, {files.FLOWHMR_CONFIG: "installed", files.FLOWHMR_CKPT: "missing"})
        self.assertEqual((self.installer.root / files.FLOWHMR_CONFIG).read_bytes(), config.read_bytes())

    def test_neutral_npz_with_unrelated_arrays_is_rejected(self):
        from zipfile import ZipFile

        neutral = self.source / "neutral"
        neutral.mkdir()
        with ZipFile(neutral / "model.npz", "w") as archive:
            archive.writestr("unrelated", b"?" * (1 << 20))
        result = self.run_import(neutral)["results"][0]
        self.assertEqual(result["file"], files.SMPLH_FILES["neutral"])
        self.assertEqual(result["code"], "invalid_format")
        self.assertFalse((self.installer.root / files.SMPLH_FILES["neutral"]).exists())

    def test_nonmodel_folder_reports_no_recognized_files(self):
        (self.source / "readme.txt").write_text("Unrelated files stay where they are.", encoding="utf-8")
        report = self.run_import()
        self.assertEqual(report["error_code"], "no_models_found")
        self.assertFalse(self.installer.root.exists())

    def test_smplh_tar_requires_another_extraction(self):
        (self.source / "smplh.tar").write_bytes(b"Not yet unpacked")
        report = self.run_import()
        self.assertEqual(report["error_code"], "extract_smplh_tar")
        self.assertFalse(report["groups"])
        self.assertFalse(self.installer.root.exists())


if __name__ == "__main__":
    unittest.main()
