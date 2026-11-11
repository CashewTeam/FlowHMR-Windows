import hashlib
from io import BytesIO
from pathlib import Path
import stat
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

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

    def run_import(self, source=None, *, kind="folder"):
        response = self.installer.start(str(source or self.source), kind=kind)
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

    def test_zip_installs_only_recognized_content_and_keeps_archive(self):
        bundle = self.source / "模型.ZIP"
        with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("models/" + self.relative, self.content)
            archive.writestr("notes/readme.txt", "Unrelated archive file")
        before = bundle.read_bytes()
        report = self.run_import(bundle, kind="archive")
        self.assertEqual(report["groups"], ["joint-regressor"])
        self.assertEqual(report["results"], [{"file": self.relative, "status": "installed"}])
        self.assertEqual(report["bytes_done"], len(self.content))
        self.assertEqual(bundle.read_bytes(), before)
        self.assertEqual(self.destination.read_bytes(), self.content)
        self.assertFalse(list(self.installer.root.rglob("readme.txt")))
        self.assertFalse((self.installer.root / model_import._ARCHIVE_STAGE).exists())

        report = self.run_import(bundle, kind="archive")
        self.assertEqual(report["results"][0]["code"], "already_installed")

    def test_tar_gzip_imports_only_the_official_hamer_members(self):
        bundle = self.source / "hamer_demo_data.tar.gz"
        contents = {}
        assets = {}
        with tarfile.open(bundle, "w:gz") as archive:
            for member, asset in model_sources.HAMER_MEMBERS.items():
                data = f"Fixture for {member}\n".encode()
                contents[asset.relative] = data
                assets[asset.relative] = model_sources.Asset(asset.relative, len(data), hashlib.sha256(data).hexdigest())
                self.add_tar(archive, "./" + member, data)
            self.add_tar(archive, "./_DATA/demo/image.png", b"not needed")
        with patch.dict(model_import._ASSETS, assets):
            report = self.run_import(bundle, kind="archive")
        self.assertEqual(report["groups"], ["hamer"])
        self.assertEqual(report["bytes_done"], bundle.stat().st_size)
        self.assertEqual({result["file"]: result["status"] for result in report["results"]},
                         {relative: "installed" for relative in contents})
        for relative, data in contents.items():
            self.assertEqual((self.installer.root / relative).read_bytes(), data)
        self.assertFalse(list(self.installer.root.rglob("image.png")))

    @staticmethod
    def add_tar(archive, path, data):
        member = tarfile.TarInfo(path)
        member.size = len(data)
        archive.addfile(member, BytesIO(data))

    def assert_rejected_archive(self, bundle, error_code):
        report = self.run_import(bundle, kind="archive")
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["error_code"], error_code)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.installer.root / model_import._ARCHIVE_STAGE).exists())

    def test_tar_traversal_discards_earlier_staged_model(self):
        bundle = self.source / "unsafe.tar.xz"
        with tarfile.open(bundle, "w:xz") as archive:
            self.add_tar(archive, self.relative, self.content)
            self.add_tar(archive, "../outside.txt", b"Must not be extracted")
        self.assert_rejected_archive(bundle, "archive_unsafe")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_zip_rejects_symbolic_and_reparse_entries(self):
        for name, attributes, system in (
                ("symlink", (stat.S_IFLNK | 0o777) << 16, 3),
                ("reparse", stat.FILE_ATTRIBUTE_REPARSE_POINT, 0)):
            with self.subTest(kind=name):
                bundle = self.source / f"{name}.zip"
                with ZipFile(bundle, "w") as archive:
                    archive.writestr(self.relative, self.content)
                    entry = ZipInfo("ignored")
                    entry.create_system, entry.external_attr = system, attributes
                    archive.writestr(entry, "../outside")
                self.assert_rejected_archive(bundle, "archive_unsafe")

    def test_tar_rejects_hard_link(self):
        bundle = self.source / "link.tar"
        with tarfile.open(bundle, "w") as archive:
            self.add_tar(archive, self.relative, self.content)
            member = tarfile.TarInfo("ignored-link")
            member.type = tarfile.LNKTYPE
            member.linkname = self.relative
            archive.addfile(member)
        self.assert_rejected_archive(bundle, "archive_unsafe")

    def test_zip_rejects_duplicate_destinations_and_case_aliases(self):
        for second in ("elsewhere/" + self.source_name, self.relative.upper()):
            with self.subTest(path=second):
                bundle = self.source / "duplicate.zip"
                with ZipFile(bundle, "w") as archive:
                    archive.writestr(self.relative, self.content)
                    archive.writestr(second, self.content)
                self.assert_rejected_archive(bundle, "archive_unsafe")

    def test_nested_archive_is_not_opened_or_staged(self):
        bundle = self.source / "outer.zip"
        with ZipFile(bundle, "w") as archive:
            archive.writestr(self.relative, self.content)
            archive.writestr("extra/smplh.tar.xz", b"untrusted nested data")
        self.assert_rejected_archive(bundle, "archive_nested")

    def test_bad_tar_header_does_not_publish_earlier_model(self):
        bundle = self.source / "corrupt.tar"
        with tarfile.open(bundle, "w") as archive:
            self.add_tar(archive, self.relative, self.content)
        data = bytearray(bundle.read_bytes())
        data[2 * tarfile.BLOCKSIZE] = ord("X")  # Corrupt the end marker after the model.
        bundle.write_bytes(data)
        self.assert_rejected_archive(bundle, "archive_invalid")

    def test_zip_expanded_limit_covers_unrecognized_members(self):
        bundle = self.source / "too-large.zip"
        with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(self.relative, self.content)
            archive.writestr("ignored.txt", b"." * 8192)
        with patch.object(model_import, "_MAX_ARCHIVE_BYTES", bundle.stat().st_size + 1):
            self.assert_rejected_archive(bundle, "archive_too_large")

    def test_rejects_invalid_kind_and_unsupported_archive_extension(self):
        bundle = self.source / "unknown.7z"
        for kind, code in (("archive", "archive_unsupported"), ("unknown", "invalid_source")):
            with self.assertRaises(model_import.ModelImportError) as raised:
                self.installer.start(str(bundle), kind=kind)
            self.assertEqual(raised.exception.code, code)
        self.assertFalse(self.installer.active)


if __name__ == "__main__":
    unittest.main()
