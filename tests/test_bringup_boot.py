"""Consumer-visible config authorization and publication failure regressions."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import test_candidate_boot as baseline_tests

candidate = baseline_tests.candidate

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bringup_deploy", PROJECT / "scripts/deploy-bringup.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class BringupBootTests(unittest.TestCase):
    VARIANT = "bringup"

    def setUp(self):
        variant, release = self.VARIANT, candidate.VARIANTS[self.VARIANT]["release"]
        self.prefix = "aurora/" + variant
        self.fixture = baseline_tests.CandidateBootTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        baseline = "CONFIG_MODULE_SIG=y\n" + "\n".join(
            (key + "=y") if key in {"CONFIG_BT_BREDR", "CONFIG_INPUT_MISC"} else ("# " + key + " is not set")
            for key in sorted(candidate.CONFIG_DELTA)) + "\n"
        updated = "CONFIG_MODULE_SIG=y\n" + "\n".join(key + "=y" for key in sorted(candidate.CONFIG_DELTA)) + "\n"
        f.receipt_path = f.root / ("etc/j716s/" + variant + ".json")
        f.persistent_path = f.root / ("etc/j716s/" + variant + "-default.json")
        f.receipt["kind"] = "j716s-" + variant + "-trial"
        f.receipt["kernel_release"] = release
        build = f.receipt["build_manifest"]
        build["kernel_release"] = release
        build["variant"] = variant
        build["patches"] = copy.deepcopy(candidate.VARIANTS[variant]["patches"])
        records = []
        for entry in f.receipt["files"]:
            records.append(f.put(f.root / "boot", entry["path"].replace("open-wifi", variant),
                                 (f.root / "boot" / entry["path"]).read_bytes()))
        for name, data in (("baseline.config", baseline), (".config", updated)):
            records.append(f.put(f.root / "boot", self.prefix + "/" + name, data.encode()))
        f.receipt["files"] = records
        if variant in candidate.BUILD_DTB_VARIANTS:
            dtb = next(item for item in records if item["path"] == self.prefix + "/t6050-j716s.dtb")
            build["artifacts"]["t6050-j716s.dtb"] = {"bytes": dtb["size"], "sha256": dtb["sha256"]}
        build["artifacts"][".config"] = {"bytes": len(updated), "sha256": hashlib.sha256(updated.encode()).hexdigest()}
        f.identity["kernel_artifacts"][".config"] = {"size_bytes": len(baseline), "sha256": hashlib.sha256(baseline.encode()).hexdigest()}
        new_modules = f.root / "usr/lib/modules" / release
        shutil.copytree(f.module_root, new_modules)
        f.module_root = new_modules
        f.chosen.write_bytes((self.prefix + ".bin\0").encode())
        f.bind_baseline()
        f.store_receipt()

    def verify(self):
        f = self.fixture
        return candidate.verify_candidate(f.root, candidate.BASELINE_RELEASE, f.identity, f.deployment, self.VARIANT)

    def validate(self):
        f = self.fixture
        return candidate.validate_running_kernel(f.root, candidate.BASELINE_RELEASE, f.identity, f.deployment,
            {candidate.TOKEN: f.receipt["trial_id"]}, running_release=candidate.VARIANTS[self.VARIANT]["release"])

    def rewrite_config(self, text):
        f = self.fixture
        entry = f.put(f.root / "boot", self.prefix + "/.config", text.encode())
        f.receipt["files"] = [entry if item["path"] == entry["path"] else item for item in f.receipt["files"]]
        f.receipt["build_manifest"]["artifacts"][".config"] = {"bytes": entry["size"], "sha256": entry["sha256"]}
        f.store_receipt()

    def test_bound_trial_and_normal_default_are_accepted(self):
        f = self.fixture
        self.assertEqual(self.verify(), f.receipt)
        self.assertIsNone(self.validate())
        f.chosen.unlink()
        f.selected_path.write_bytes((f.root / "boot" / (self.prefix + ".bin")).read_bytes())
        f.persistent = {"schema": 1, "kind": "j716s-" + self.VARIANT + "-default",
                        "kernel_release": candidate.VARIANTS[self.VARIANT]["release"],
                        "candidate_receipt_sha256": candidate.digest(f.receipt_path),
                        "baseline_payload_sha256": f.deployment["root_payload_sha256"],
                        "selected_payload_sha256": candidate.digest(f.selected_path)}
        f.store_persistent()
        self.assertIsNone(self.validate())
        self.assertIsNone(f.validate_selection())

    def test_running_default_accepts_only_an_authorized_newer_selection(self):
        self.test_bound_trial_and_normal_default_are_accepted()
        f = self.fixture
        # A rollback to the baseline payload awaits reboot: the running default stays valid.
        f.selected_path.write_bytes(b"baseline payload")
        self.assertIsNone(self.validate())
        f.selected_path.write_bytes(b"unauthorized payload")
        with self.assertRaisesRegex(RuntimeError, "no unique pinned authorization"):
            self.validate()

    def test_hash_rebound_unrelated_config_change_is_refused(self):
        text = (self.fixture.root / "boot" / self.prefix / ".config").read_text()
        self.rewrite_config(text.replace("CONFIG_MODULE_SIG=y", "# CONFIG_MODULE_SIG is not set"))
        with self.assertRaisesRegex(RuntimeError, "unrelated kernel config"):
            self.verify()

    def test_missing_builtin_profile_capability_is_refused(self):
        text = (self.fixture.root / "boot" / self.prefix / ".config").read_text()
        self.rewrite_config(text.replace("CONFIG_BT_RFCOMM=y", "CONFIG_BT_RFCOMM=m"))
        with self.assertRaisesRegex(RuntimeError, "must be built in"):
            self.verify()

    def test_baseline_config_tampering_is_refused(self):
        path = self.fixture.root / "boot" / self.prefix / "baseline.config"
        path.write_text(path.read_text() + "CONFIG_UNRELATED=y\n")
        with self.assertRaisesRegex(RuntimeError, "candidate file differs"):
            self.verify()

    def test_unreviewed_patch_and_wrong_trial_are_refused(self):
        f = self.fixture
        f.receipt["build_manifest"]["patches"]["extra.patch"] = "1" * 64
        f.store_receipt()
        with self.assertRaisesRegex(RuntimeError, "build pins"):
            self.verify()
        del f.receipt["build_manifest"]["patches"]["extra.patch"]
        f.store_receipt()
        f.chosen.write_bytes(b"aurora/open-wifi.bin\0")
        with self.assertRaisesRegex(RuntimeError, "one-shot trial"):
            self.validate()

    def test_duplicate_config_assignment_is_refused(self):
        text = (self.fixture.root / "boot" / self.prefix / ".config").read_text()
        self.rewrite_config(text + "CONFIG_BT_LE=y\n")
        with self.assertRaisesRegex(RuntimeError, "duplicate kernel config"):
            self.verify()


class TouchidBootTests(BringupBootTests):
    """touchid shares bringup's config rules; its DTB is its own build's."""
    VARIANT = "touchid"

    def test_dtb_rebound_only_to_receipt_is_refused(self):
        f = self.fixture
        entry = f.put(f.root / "boot", self.prefix + "/t6050-j716s.dtb", b"unbound sensor DTB")
        f.receipt["files"] = [entry if item["path"] == entry["path"] else item for item in f.receipt["files"]]
        f.store_receipt()
        with self.assertRaisesRegex(RuntimeError, "DTB build binding"):
            self.verify()

    def test_build_bound_sensor_dtb_is_accepted(self):
        f = self.fixture
        entry = f.put(f.root / "boot", self.prefix + "/t6050-j716s.dtb", b"sensor DTB")
        f.receipt["files"] = [entry if item["path"] == entry["path"] else item for item in f.receipt["files"]]
        f.receipt["build_manifest"]["artifacts"]["t6050-j716s.dtb"] = {"bytes": entry["size"], "sha256": entry["sha256"]}
        f.store_receipt()
        self.assertEqual(self.verify(), f.receipt)


class BringupPublicationTests(unittest.TestCase):
    def test_validation_failure_restores_previous_default_not_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, selected, previous = (root / name for name in ("bringup.bin", "boot.bin", "previous.bin"))
            source.write_bytes(b"new candidate")
            selected.write_bytes(b"working open-wifi")
            previous.write_bytes(selected.read_bytes())

            def reject():
                raise RuntimeError("post-publication authorization failure")

            with self.assertRaisesRegex(RuntimeError, "authorization failure"):
                deploy.publish(source, selected, previous, candidate.digest(previous), candidate.digest(source), reject)
            self.assertEqual(selected.read_bytes(), b"working open-wifi")

    def test_candidate_hash_failure_preserves_previous_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, selected, previous = (root / name for name in ("bringup.bin", "boot.bin", "previous.bin"))
            source.write_bytes(b"tampered")
            selected.write_bytes(b"working open-wifi")
            previous.write_bytes(selected.read_bytes())
            with self.assertRaises(ValueError):
                deploy.publish(source, selected, previous, candidate.digest(previous), "0" * 64, lambda: None)
            self.assertEqual(selected.read_bytes(), b"working open-wifi")


if __name__ == "__main__":
    unittest.main()
