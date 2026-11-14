"""Consumer-visible identity invariants for destructive fresh-layout gates."""
import copy
import hashlib
import importlib.util
import json
import pathlib
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "outputs/deployment"))
from partition_metadata import added_partition
from preflight import manifest_build


class PartitionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.before = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk7", "Partitions": [
            {"DeviceIdentifier": "disk7s1", "DiskUUID": "aaaaaaaa-0000-0000-0000-000000000001",
             "Content": "Apple_APFS", "Size": 80_000_000_000},
            {"DeviceIdentifier": "disk7s2", "DiskUUID": "aaaaaaaa-0000-0000-0000-000000000002",
             "Content": "Apple_APFS_Recovery", "Size": 5_000_000_000}]}]}
        self.after = copy.deepcopy(self.before)
        partitions = self.after["AllDisksAndPartitions"][0]["Partitions"]
        partitions[1]["DeviceIdentifier"] = "disk7s3"
        partitions.insert(1, {"DeviceIdentifier": "disk7s2", "DiskUUID": "bbbbbbbb-0000-0000-0000-000000000003",
                              "Content": "EFI", "Size": 500_000_000})

    def identify(self):
        return added_partition(self.before, self.after, "disk7", "EFI", 500_000_000)

    def test_renumbering_retains_identity_and_skips_unallocated_space(self):
        self.before["AllDisksAndPartitions"][0]["Partitions"].append({"Content": "Free Space", "Size": 2_000_000_000})
        receipt = self.identify()
        self.assertEqual(receipt["partition_uuid"], "BBBBBBBB-0000-0000-0000-000000000003")
        self.assertEqual(receipt["device_at_creation"], "disk7s2")

    def test_changed_existing_partition_refuses_format_target(self):
        for key, value in (("Content", "EFI"), ("Size", 79_000_000_000),
                           ("DiskUUID", "cccccccc-0000-0000-0000-000000000004")):
            with self.subTest(key=key):
                after = copy.deepcopy(self.after)
                after["AllDisksAndPartitions"][0]["Partitions"][0][key] = value
                with self.assertRaises(ValueError):
                    added_partition(self.before, after, "disk7", "EFI", 500_000_000)

    def test_ambiguous_guids_refuse_format_target(self):
        partitions = self.after["AllDisksAndPartitions"][0]["Partitions"]
        partitions[1]["DiskUUID"] = partitions[0]["DiskUUID"].upper()
        with self.assertRaises(ValueError):
            self.identify()

    def test_wrong_added_type_or_size_refuses_format_target(self):
        for key, value in (("Content", "Apple_APFS_Recovery"), ("Size", 5_000_000_000)):
            with self.subTest(key=key):
                after = copy.deepcopy(self.after)
                after["AllDisksAndPartitions"][0]["Partitions"][1][key] = value
                with self.assertRaises(ValueError):
                    added_partition(self.before, after, "disk7", "EFI", 500_000_000)


class RestoreIdentityTests(unittest.TestCase):
    def manifest(self, *builds):
        return {"BuildIdentities": [{"Info": {"DeviceClass": "j716sap", **(
            {"BuildNumber": build} if build is not None else {})}} for build in builds]}

    def test_installed_manifest_without_top_level_build(self):
        self.assertEqual(manifest_build(self.manifest("26C5035r", "26C5035r")), "26C5035r")

    def test_missing_or_conflicting_board_identity_refuses_source(self):
        for builds in (("26C5035r", None), ("26C5035r", "25E246"), (None,)):
            with self.subTest(builds=builds), self.assertRaises(ValueError):
                manifest_build(self.manifest(*builds))

    def test_consistent_top_level_only_format_and_conflict(self):
        manifest = self.manifest(None, None)
        manifest["ProductBuildVersion"] = "26C5035r"
        self.assertEqual(manifest_build(manifest), "26C5035r")
        manifest = self.manifest("25E246")
        manifest["ProductBuildVersion"] = "26C5035r"
        with self.assertRaises(ValueError):
            manifest_build(manifest)

    def test_other_board_does_not_establish_j716s_identity(self):
        with self.assertRaises(ValueError):
            manifest_build({"ProductBuildVersion": "26C5035r", "BuildIdentities": [
                {"Info": {"DeviceClass": "j714sap", "BuildNumber": "26C5035r"}}]})


class RescueDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = pathlib.Path(self.temporary.name)
        spec = importlib.util.spec_from_file_location("finish_dependencies",
            pathlib.Path(__file__).resolve().parents[1] / "outputs/deployment/finish-install-j716s.py")
        self.finish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.finish)
        self.stage2 = self.directory / "rescue.bin"
        self.esp = "aaaaaaaa-0000-0000-0000-000000000001"
        self.files = []
        entries = (("Image.gz", "outputs/kernel-candidate/Image.gz", b"kernel"),
                   ("t6050-j716s.dtb", "outputs/boot-candidate/t6050-j716s-kis02.dtb", b"tree"),
                   ("initramfs.cpio.gz", "outputs/boot-candidate/j716s-rescue-initramfs.cpio.gz", b"ramfs"))
        for name, source, data in entries:
            relative = "aurora/rescue/" + name
            path = self.directory / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.files.append({"path": relative, "source": source, "size": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()})
        self.finish.PROJECT = self.directory
        self.manifest = self.stage2.with_suffix(".bin.json")
        self.manifest.write_text(json.dumps({"schema": 1, "esp_partuuid": self.esp, "files": self.files}))
        mocked = patch.object(self.finish, "artifacts", return_value=(b"", self.stage2))
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_changed_or_missing_dependency_refuses_unchanged_loader(self):
        path = self.directory / self.files[0]["source"]
        for content in (b"altered kernel", None):
            with self.subTest(content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                with self.assertRaises(ValueError):
                    self.finish.payload_files(self.esp)

    def test_wrong_esp_or_changed_manifest_refuses_bundle(self):
        with self.assertRaises(ValueError):
            self.finish.payload_files("aaaaaaaa-0000-0000-0000-000000000002")
        changed = json.loads(self.manifest.read_text())
        changed["files"][0]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            self.finish.payload_files(self.esp)


if __name__ == "__main__":
    unittest.main()
