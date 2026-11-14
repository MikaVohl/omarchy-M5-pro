"""Consumer-visible authorization tests using an isolated root/ESP tree."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs/persistent-install"))
import candidate_boot as candidate


class CandidateBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipt_path = self.root / "etc/j716s/open-wifi.json"
        self.persistent_path = self.root / "etc/j716s/open-wifi-default.json"
        self.persistent_uid = 0
        self.receipt_uid = 0
        original_stat = Path.stat

        def fixture_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path in (self.receipt_path, self.persistent_path):
                # Model root ownership without requiring privileged test execution.
                fields = list(result)
                fields[4] = self.receipt_uid if path == self.receipt_path else self.persistent_uid
                return os.stat_result(fields)
            return result

        self.ownership = patch.object(Path, "stat", fixture_stat)
        self.ownership.start()
        self.addCleanup(self.ownership.stop)
        self.identity = {"kernel_release": candidate.BASELINE_RELEASE, "kernel_artifacts": {}}
        artifacts = {name: {"bytes": 1, "sha256": "1" * 64} for name in candidate.BUILD_ARTIFACTS}
        for name in (".config", "t6050-j716s.dtb"):
            data = ("baseline raw " + name).encode()
            self.identity["kernel_artifacts"][name] = {
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            artifacts[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.deployment = {"kernel_release": candidate.BASELINE_RELEASE, "payload_files": []}
        self.receipt = {
            "schema": 1, "kind": "j716s-open-wifi-trial", "trial_id": "a" * 64,
            "baseline_identity_sha256": "", "baseline_deployment_sha256": "",
            "kernel_release": candidate.CANDIDATE_RELEASE,
            "build_manifest": {
                "schema": 1, "commit": candidate.KERNEL_COMMIT,
                "kernel_release": candidate.CANDIDATE_RELEASE,
                "variant": "open-wifi", "qualification": "unqualified-experimental",
                "patches": {"kernel-j716s-open-wifi.patch": candidate.PATCH_SHA256},
                "artifacts": artifacts}, "files": [], "modules": []}
        for name in sorted(candidate.BOOT_FILES):
            entry = self.put(self.root / "boot", name, ("candidate " + name).encode())
            self.receipt["files"].append(entry)
            if name.endswith("/Image.gz"):
                artifacts["Image.gz"] = {"bytes": entry["size"], "sha256": entry["sha256"]}
            if name.endswith(("/t6050-j716s.dtb", "/initramfs.cpio.gz")):
                baseline = dict(entry, path=name.replace("open-wifi/", "root/"))
                self.deployment["payload_files"].append(baseline)
                self.put(self.root / "boot", baseline["path"], ("candidate " + name).encode())
        self.module_root = self.root / "usr/lib/modules" / candidate.CANDIDATE_RELEASE
        for name in ("kernel/drivers/net/wireless/brcmfmac.ko", "modules.dep"):
            self.receipt["modules"].append(self.put(self.module_root, name, name.encode()))
        baseline = self.put(self.root / "boot", "aurora/root.bin", b"baseline payload")
        self.deployment["root_payload_sha256"] = baseline["sha256"]
        self.selected_path = self.root / "boot/aurora/boot.bin"
        self.selected_path.write_bytes(b"baseline payload")
        self.bind_baseline()
        self.store_receipt()
        self.chosen = self.root / "sys/firmware/devicetree/base/chosen/aurora,trial"
        self.chosen.parent.mkdir(parents=True)
        self.chosen.write_bytes(b"aurora/open-wifi.bin\0")

    def put(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def bind_baseline(self):
        for relative, field, value in (
                ("usr/share/j716s/image-identity.json", "baseline_identity_sha256", self.identity),
                ("boot/aurora/deployment.json", "baseline_deployment_sha256", self.deployment)):
            entry = self.put(self.root, relative, json.dumps(value).encode())
            self.receipt[field] = entry["sha256"]

    def store_receipt(self):
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        self.receipt_path.write_text(json.dumps(self.receipt))
        self.receipt_path.chmod(0o644)

    def store_persistent(self):
        self.persistent_path.write_text(json.dumps(self.persistent))
        self.persistent_path.chmod(0o644)

    def prepare_persistent(self):
        self.chosen.unlink()
        self.selected_path.write_bytes((self.root / "boot/aurora/open-wifi.bin").read_bytes())
        self.persistent = {
            "schema": 1, "kind": "j716s-open-wifi-default",
            "kernel_release": candidate.CANDIDATE_RELEASE,
            "candidate_receipt_sha256": candidate.digest(self.receipt_path),
            "baseline_payload_sha256": self.deployment["root_payload_sha256"],
            "selected_payload_sha256": candidate.digest(self.selected_path)}
        self.store_persistent()

    def validate_selection(self):
        return candidate.validate_boot_selection(
            self.root, candidate.BASELINE_RELEASE, self.identity, self.deployment)

    def rejects_persistent(self):
        for validate in (self.validate, self.validate_selection):
            with self.subTest(validator=validate.__name__):
                with self.assertRaises((RuntimeError, OSError, ValueError, KeyError)):
                    validate()

    def validate(self, release=candidate.CANDIDATE_RELEASE, cmdline=None):
        return candidate.validate_running_kernel(
            self.root, candidate.BASELINE_RELEASE, self.identity, self.deployment,
            {candidate.TOKEN: self.receipt["trial_id"]} if cmdline is None else cmdline,
            running_release=release)

    def rejects(self):
        self.store_receipt()
        with self.assertRaises((RuntimeError, OSError, ValueError, KeyError)):
            self.validate()

    def test_accepts_bound_candidate_and_stored_preflight(self):
        self.assertIsNone(self.validate())
        self.chosen.unlink()
        self.assertEqual(candidate.verify_candidate(
            self.root, candidate.BASELINE_RELEASE, self.identity, self.deployment), self.receipt)

    def test_proc_chosen_fallback_and_nul_stripping(self):
        self.chosen.unlink()
        fallback = self.root / "proc/device-tree/chosen/aurora,trial"
        fallback.parent.mkdir(parents=True)
        fallback.write_bytes(b"\0aurora/open-wifi.bin\0\0")
        self.assertIsNone(self.validate())

    def test_primary_chosen_cannot_be_overridden_by_fallback(self):
        self.chosen.write_bytes(b"aurora/root.bin\0")
        fallback = self.root / "proc/device-tree/chosen/aurora,trial"
        fallback.parent.mkdir(parents=True)
        fallback.write_bytes(b"aurora/open-wifi.bin\0")
        self.rejects()

    def test_invalid_or_missing_trial_property_is_rejected_without_persistent_receipt(self):
        for value in (b"", b"aurora/root.bin\0", b" aurora/open-wifi.bin\0"):
            with self.subTest(value=value):
                self.chosen.write_bytes(value)
                self.rejects()
        self.chosen.unlink()
        self.rejects()

    def test_persistent_candidate_without_trial_property_is_accepted(self):
        self.prepare_persistent()
        for validate in (self.validate, self.validate_selection):
            with self.subTest(validator=validate.__name__):
                self.assertIsNone(validate())

    def test_persistent_receipt_is_required_for_candidate_selection(self):
        self.prepare_persistent()
        self.persistent_path.unlink()
        self.rejects_persistent()

    def test_persistent_receipt_fields_and_bindings_are_strict(self):
        self.prepare_persistent()
        original = dict(self.persistent)
        changes = (
            ("schema", True), ("schema", 2), ("kind", "j716s-open-wifi-trial"),
            ("kernel_release", candidate.BASELINE_RELEASE),
            ("candidate_receipt_sha256", "0" * 64),
            ("baseline_payload_sha256", "0" * 64),
            ("selected_payload_sha256", "0" * 64),
            ("selected_payload_sha256", "not-a-digest"), ("extra", "unsupported"))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.persistent = dict(original, **{key: value})
                self.store_persistent()
                self.rejects_persistent()
        for key in original:
            with self.subTest(missing=key):
                self.persistent = dict(original)
                del self.persistent[key]
                self.store_persistent()
                self.rejects_persistent()
        self.persistent_path.write_text("{")
        self.rejects_persistent()

    def test_persistent_receipt_binds_exact_candidate_receipt_bytes(self):
        self.prepare_persistent()
        self.receipt_path.write_bytes(self.receipt_path.read_bytes() + b" ")
        self.rejects_persistent()

    def test_persistent_receipt_requires_root_owner_and_safe_permissions(self):
        self.prepare_persistent()
        self.persistent_uid = 1000
        self.rejects_persistent()
        self.persistent_uid = 0
        for mode in (0o664, 0o646, 0o666):
            with self.subTest(mode=mode):
                self.persistent_path.chmod(mode)
                self.rejects_persistent()
        self.persistent_path.chmod(0o600)
        self.assertIsNone(self.validate())
        self.assertIsNone(self.validate_selection())

    def test_persistent_receipt_and_selected_payload_reject_symlink_paths(self):
        self.prepare_persistent()
        for path in (self.persistent_path, self.selected_path,
                     self.persistent_path.parent, self.selected_path.parent):
            with self.subTest(path=path):
                moved = path.with_name(path.name + ".saved")
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=moved.is_dir())
                self.rejects_persistent()
                path.unlink()
                moved.rename(path)

    def test_persistent_receipt_must_be_a_regular_file(self):
        self.prepare_persistent()
        self.persistent_path.unlink()
        self.persistent_path.mkdir()
        self.rejects_persistent()

    def test_unknown_selected_payload_is_rejected(self):
        self.prepare_persistent()
        self.selected_path.write_bytes(b"unknown stable payload")
        self.rejects_persistent()

    def test_persistent_receipt_does_not_override_existing_trial_property(self):
        self.prepare_persistent()
        for value in (b"", b"aurora/root.bin\0", b" aurora/open-wifi.bin\0"):
            with self.subTest(value=value):
                self.chosen.write_bytes(value)
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_persistent_candidate_still_requires_exact_kernel_and_trial_id(self):
        self.prepare_persistent()
        for release, cmdline in (
                (candidate.CANDIDATE_RELEASE, {}),
                (candidate.CANDIDATE_RELEASE, {candidate.TOKEN: "b" * 64}),
                (candidate.CANDIDATE_RELEASE + "-other", {candidate.TOKEN: "a" * 64}),
                (candidate.BASELINE_RELEASE, {candidate.TOKEN: "a" * 64})):
            with self.subTest(release=release, cmdline=cmdline):
                with self.assertRaises(RuntimeError):
                    self.validate(release, cmdline)

    def test_baseline_selection_rollback_does_not_require_candidate_validation(self):
        self.prepare_persistent()
        self.selected_path.write_bytes((self.root / "boot/aurora/root.bin").read_bytes())
        self.receipt_path.unlink()
        with patch.object(candidate, "verify_candidate",
                          side_effect=AssertionError("baseline must not scan candidate")):
            self.assertIsNone(self.validate_selection())
        self.assertTrue(self.persistent_path.is_file())
        self.assertIsNone(self.validate(candidate.BASELINE_RELEASE, {}))

    def test_baseline_boot_remains_available_without_candidate_files(self):
        self.assertIsNone(candidate.validate_running_kernel(
            self.root / "absent", candidate.BASELINE_RELEASE, None, None, {},
            running_release=candidate.BASELINE_RELEASE))

    def test_no_downgrade_or_unbound_candidate(self):
        for release, cmdline in ((candidate.BASELINE_RELEASE, {candidate.TOKEN: "a" * 64}),
                                 (candidate.CANDIDATE_RELEASE, {}),
                                 (candidate.CANDIDATE_RELEASE, {candidate.TOKEN: "b" * 64}),
                                 (candidate.CANDIDATE_RELEASE, {candidate.TOKEN: "A" * 64}),
                                 (candidate.CANDIDATE_RELEASE + "-other", {}),
                                 ("6.0-old", {})):
            with self.subTest(release=release, cmdline=cmdline), self.assertRaises(RuntimeError):
                self.validate(release, cmdline)


    def test_tampered_baseline_identity_and_deployment(self):
        for relative in ("usr/share/j716s/image-identity.json", "boot/aurora/deployment.json"):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                self.rejects()
                path.write_bytes(original)

    def test_unbound_baseline_objects_are_rejected(self):
        self.identity["extra"] = "not on disk"
        self.rejects()

    def test_tampered_candidate_files_and_sizes(self):
        for entry in self.receipt["files"]:
            with self.subTest(path=entry["path"]):
                path = self.root / "boot" / entry["path"]
                original = path.read_bytes()
                path.write_bytes(b"x" * len(original))
                self.rejects()
                path.write_bytes(original + b"extra")
                self.rejects()
                path.write_bytes(original)

    def test_candidate_build_and_baseline_dependency_crossbinding(self):
        original = copy.deepcopy(self.receipt)
        targets = [self.receipt["build_manifest"]["artifacts"]["Image.gz"],
                   self.receipt["build_manifest"]["artifacts"][".config"],
                   self.receipt["build_manifest"]["artifacts"]["t6050-j716s.dtb"]]
        for target in targets:
            with self.subTest(target=target):
                old = target["sha256"]
                target["sha256"] = "0" * 64
                self.rejects()
                target["sha256"] = old
        for name in ("t6050-j716s.dtb", "initramfs.cpio.gz"):
            entry = next(e for e in self.receipt["files"] if e["path"].endswith("/" + name))
            path = self.root / "boot" / entry["path"]
            original_data = path.read_bytes()
            entry.update(self.put(self.root / "boot", entry["path"], b"different, consistently inventoried"))
            self.rejects()
            path.write_bytes(original_data)
            self.receipt = copy.deepcopy(original)

    def test_full_manifest_pins_schema_and_trial_id_are_required(self):
        original = copy.deepcopy(self.receipt)
        changes = (("commit", "0" * 40), ("variant", "baseline"),
                   ("qualification", "qualified"), ("patches", {}),
                   ("kernel_release", candidate.BASELINE_RELEASE), ("schema", 2),
                   ("artifacts", {}))
        for key, value in changes:
            with self.subTest(key=key):
                self.receipt = copy.deepcopy(original)
                self.receipt["build_manifest"][key] = value
                self.rejects()
        for key, value in (("schema", 2), ("kind", "persistent"),
                           ("kernel_release", candidate.BASELINE_RELEASE), ("trial_id", "not-a-nonce")):
            with self.subTest(key=key):
                self.receipt = copy.deepcopy(original)
                self.receipt[key] = value
                self.rejects()

    def test_module_tamper_extra_missing_and_special_files(self):
        entry = self.receipt["modules"][0]
        path = self.module_root / entry["path"]
        original = path.read_bytes()
        path.write_bytes(b"x" * len(original))
        self.rejects()
        path.unlink()
        self.rejects()
        path.write_bytes(original)
        extra = self.module_root / "unrecorded"
        extra.write_bytes(b"extra")
        self.rejects()
        extra.unlink()
        os.mkfifo(extra)
        self.rejects()

    def test_traversal_noncanonical_duplicate_and_extra_inventory(self):
        original = copy.deepcopy(self.receipt)
        for collection in ("files", "modules"):
            for bad in ("../outside", "/absolute", "a/../outside", "a//b", "./a", "a\\b"):
                with self.subTest(collection=collection, path=bad):
                    self.receipt = copy.deepcopy(original)
                    self.receipt[collection][0]["path"] = bad
                    self.rejects()
            self.receipt = copy.deepcopy(original)
            self.receipt[collection].append(copy.deepcopy(self.receipt[collection][0]))
            self.rejects()
        self.receipt = copy.deepcopy(original)
        self.receipt["files"].pop()
        self.rejects()

    def test_symlink_files_and_parent_directories_are_rejected(self):
        paths = [self.root / "boot" / self.receipt["files"][0]["path"],
                 self.module_root / self.receipt["modules"][0]["path"], self.receipt_path,
                 self.root / "boot/aurora/open-wifi", self.module_root / "kernel",
                 self.root / "usr/share/j716s"]
        for path in paths:
            with self.subTest(path=path):
                moved = path.with_name(path.name + ".saved")
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=moved.is_dir())
                with self.assertRaises(RuntimeError):
                    self.validate()
                path.unlink()
                moved.rename(path)
        extra = self.module_root / "unrecorded-link"
        extra.symlink_to(self.module_root / "modules.dep")
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_receipt_requires_root_owner_and_safe_permissions(self):
        self.receipt_uid = 1000
        with self.assertRaises(RuntimeError):
            self.validate()
        self.receipt_uid = 0
        for mode in (0o664, 0o646, 0o666):
            with self.subTest(mode=mode):
                self.receipt_path.chmod(mode)
                with self.assertRaises(RuntimeError):
                    self.validate()
        self.receipt_path.chmod(0o600)
        self.assertIsNone(self.validate())


if __name__ == "__main__":
    unittest.main()
