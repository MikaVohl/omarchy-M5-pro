"""Isolated consumer authorization regressions for discovery-only SEP trials."""
import copy
import shutil
import unittest

import test_candidate_boot as baseline_tests
import test_bringup_boot as bringup_tests

candidate = baseline_tests.candidate

# Independent expected safety policy: do not derive this fixture from production.
SAFE_PARAMETERS = {
    "apple_sep.xart_writes": "0", "apple_sep.provision_keybag": "0",
    "apple_sep.xart_guard": "2", "apple_sep.trusted_keys": "0",
    "apple_sep.filevault_api": "0", "apple_sep.keystore_only": "1",
}


class SepProbeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = baseline_tests.CandidateBootTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.receipt_path = f.root / "etc/j716s/sep-probe.json"
        f.persistent_path = f.root / "etc/j716s/sep-probe-default.json"
        f.receipt["kind"] = "j716s-sep-probe-trial"
        f.receipt["kernel_release"] = candidate.SEP_PROBE_RELEASE
        build = f.receipt["build_manifest"]
        build["variant"] = "sep-probe"
        build["kernel_release"] = candidate.SEP_PROBE_RELEASE
        build["patches"] = copy.deepcopy(candidate.VARIANTS["sep-probe"]["patches"])
        records = []
        for entry in f.receipt["files"]:
            data = (f.root / "boot" / entry["path"]).read_bytes()
            if entry["path"].endswith("/t6050-j716s.dtb"):
                data = b"SEP discovery DTB distinct from baseline"
            record = f.put(f.root / "boot", entry["path"].replace("open-wifi", "sep-probe"), data)
            records.append(record)
            if entry["path"].endswith("/t6050-j716s.dtb"):
                build["artifacts"]["t6050-j716s.dtb"] = {"bytes": record["size"], "sha256": record["sha256"]}
        f.receipt["files"] = records
        modules = f.root / "usr/lib/modules" / candidate.SEP_PROBE_RELEASE
        shutil.copytree(f.module_root, modules)
        f.module_root = modules
        f.chosen.write_bytes(b"aurora/sep-probe.bin\0")
        f.store_receipt()

    def verify(self):
        f = self.fixture
        return candidate.verify_candidate(f.root, candidate.BASELINE_RELEASE, f.identity, f.deployment, "sep-probe")

    def validate(self, parameters=None):
        f = self.fixture
        cmdline = dict(SAFE_PARAMETERS if parameters is None else parameters)
        cmdline[candidate.TOKEN] = f.receipt["trial_id"]
        return candidate.validate_running_kernel(f.root, candidate.BASELINE_RELEASE, f.identity, f.deployment,
                                                cmdline, running_release=candidate.SEP_PROBE_RELEASE)

    def rewrite_dependency(self, name, data, bind_build=False):
        f = self.fixture
        entry = f.put(f.root / "boot", "aurora/sep-probe/" + name, data)
        f.receipt["files"] = [entry if item["path"] == entry["path"] else item for item in f.receipt["files"]]
        if bind_build:
            f.receipt["build_manifest"]["artifacts"][name] = {"bytes": entry["size"], "sha256": entry["sha256"]}
        f.store_receipt()

    def install_default_receipt(self):
        f = self.fixture
        f.selected_path.write_bytes((f.root / "boot/aurora/sep-probe.bin").read_bytes())
        f.persistent = {
            "schema": 1, "kind": "j716s-sep-probe-default", "kernel_release": candidate.SEP_PROBE_RELEASE,
            "candidate_receipt_sha256": candidate.digest(f.receipt_path),
            "baseline_payload_sha256": f.deployment["root_payload_sha256"],
            "selected_payload_sha256": candidate.digest(f.selected_path),
        }
        f.store_persistent()

    def test_build_bound_changed_dtb_trial_and_stored_preflight_are_accepted(self):
        f = self.fixture
        self.assertEqual(self.verify(), f.receipt)
        self.assertIsNone(self.validate())
        f.chosen.unlink()
        self.assertEqual(self.verify(), f.receipt)

    def test_dtb_rebound_only_to_receipt_is_rejected(self):
        self.rewrite_dependency("t6050-j716s.dtb", b"unbound changed DTB")
        with self.assertRaisesRegex(RuntimeError, "DTB build binding"):
            self.validate()

    def test_dtb_manifest_hash_and_byte_binding_are_both_required(self):
        f = self.fixture
        original = copy.deepcopy(f.receipt["build_manifest"]["artifacts"]["t6050-j716s.dtb"])
        for key, value in (("bytes", original["bytes"] + 1), ("sha256", "0" * 64)):
            with self.subTest(key=key):
                f.receipt["build_manifest"]["artifacts"]["t6050-j716s.dtb"] = dict(original, **{key: value})
                f.store_receipt()
                with self.assertRaisesRegex(RuntimeError, "DTB build binding"):
                    self.verify()

    def test_dtb_file_tampering_is_rejected(self):
        (self.fixture.root / "boot/aurora/sep-probe/t6050-j716s.dtb").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "candidate file differs"):
            self.validate()

    def test_config_hash_and_bytes_must_remain_exact_baseline(self):
        f = self.fixture
        original = copy.deepcopy(f.receipt["build_manifest"]["artifacts"][".config"])
        for key, value in (("bytes", original["bytes"] + 1), ("sha256", "0" * 64)):
            with self.subTest(key=key):
                f.receipt["build_manifest"]["artifacts"][".config"] = dict(original, **{key: value})
                f.store_receipt()
                with self.assertRaisesRegex(RuntimeError, "baseline build artifact: .config"):
                    self.validate()

    def test_rebound_initramfs_change_is_rejected(self):
        self.rewrite_dependency("initramfs.cpio.gz", b"unauthorized initramfs")
        with self.assertRaisesRegex(RuntimeError, "baseline boot dependency: initramfs"):
            self.validate()

    def test_every_missing_or_unsafe_safety_parameter_is_rejected(self):
        for key, expected in SAFE_PARAMETERS.items():
            for value in (None, "1" if expected == "0" else "0", "", expected + ",0", int(expected)):
                with self.subTest(key=key, value=value):
                    parameters = dict(SAFE_PARAMETERS)
                    if value is None:
                        del parameters[key]
                    else:
                        parameters[key] = value
                    with self.assertRaisesRegex(RuntimeError, "safe discovery command-line flags"):
                        self.validate(parameters)

    def test_matching_chosen_is_mandatory_even_with_bound_default(self):
        f = self.fixture
        for value in (b"", b"aurora/open-wifi.bin\0", b"aurora/root.bin\0"):
            with self.subTest(value=value):
                f.chosen.write_bytes(value)
                with self.assertRaisesRegex(RuntimeError, "one-shot trial"):
                    self.validate()
        f.chosen.unlink()
        with self.assertRaisesRegex(RuntimeError, "matching one-shot trial"):
            self.validate()
        self.install_default_receipt()
        with self.assertRaisesRegex(RuntimeError, "no persistent authorization"):
            self.validate()
        with self.assertRaisesRegex(RuntimeError, "no persistent authorization"):
            f.validate_selection()

    def test_persistent_receipt_is_rejected_even_with_matching_chosen(self):
        self.install_default_receipt()
        with self.assertRaisesRegex(RuntimeError, "no persistent authorization"):
            self.validate()
        f = self.fixture
        f.selected_path.write_bytes(b"baseline payload")
        with self.assertRaisesRegex(RuntimeError, "no persistent authorization"):
            f.validate_selection()

    def test_probe_selected_without_default_receipt_is_rejected(self):
        f = self.fixture
        f.selected_path.write_bytes((f.root / "boot/aurora/sep-probe.bin").read_bytes())
        with self.assertRaisesRegex(RuntimeError, "no unique pinned authorization"):
            f.validate_selection()

    def test_proc_chosen_fallback_authorizes_only_matching_trial(self):
        f = self.fixture
        f.chosen.unlink()
        fallback = f.root / "proc/device-tree/chosen/aurora,trial"
        fallback.parent.mkdir(parents=True)
        fallback.write_bytes(b"aurora/sep-probe.bin\0")
        self.assertIsNone(self.validate())
        fallback.write_bytes(b"aurora/bringup.bin\0")
        with self.assertRaisesRegex(RuntimeError, "one-shot trial"):
            self.validate()

    def test_full_manifest_patch_pin_remains_required(self):
        f = self.fixture
        f.receipt["build_manifest"]["patches"]["kernel-j716s-sep-probe.patch"] = "0" * 64
        f.store_receipt()
        with self.assertRaisesRegex(RuntimeError, "build pins"):
            self.verify()


class NormalVariantRegressionTests(unittest.TestCase):
    def test_baseline_and_openwifi_trial_and_default_still_authorize(self):
        f = baseline_tests.CandidateBootTests(methodName="runTest")
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.assertIsNone(f.validate(release=candidate.BASELINE_RELEASE, cmdline={}))
        self.assertIsNone(f.validate_selection())
        self.assertIsNone(f.validate())
        f.prepare_persistent()
        self.assertIsNone(f.validate())
        self.assertIsNone(f.validate_selection())

    def test_bringup_trial_and_default_still_authorize(self):
        fixture = bringup_tests.BringupBootTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.test_bound_trial_and_normal_default_are_accepted()


if __name__ == "__main__":
    unittest.main()
