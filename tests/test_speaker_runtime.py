"""Offline runtime packaging regressions; no hardware, services or Apple data."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("speaker_runtime", Path(__file__).resolve().parents[1] / "scripts/prepare-speaker-runtime.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TuningAdmission(unittest.TestCase):
    def setUp(self):
        self.request = {"unit_id": "a" * 64}
        self.ident = {"verified": True, "kind": "Installer", "board": "j716s", "unit_bound": True,
                      "unit_id": "a" * 64, "macos_build": m.ref.BUILD, "aid": "AID41", "speaker_id": 3}
        self.dump = {"tuning": {"refusals": [], "lsid": None,
                              "protection": {"kind": "spp3", "lanes": list(range(6))},
                              "volume_range": {"min_db": -60.0, "max_db": 0.0, "source": "Config/AID41.plist"}},
                     "diagnostics": {"items": []}}
        self.gate = {"units_at_parity": ["spp3", "lsm2", "atsp"]}

    def check(self):
        m.admit_tuning(self.ident, self.dump, self.request, self.gate)


    def test_foreign_or_unverified_bundle_is_refused(self):
        for field, value in (("unit_id", "b" * 64), ("board", "j616s"), ("speaker_id", 0),
                             ("verified", False), ("unit_bound", False), ("macos_build", "25G83")):
            with self.subTest(field=field):
                original = self.ident[field]
                self.ident[field] = value
                with self.assertRaisesRegex(ValueError, "paired J716s"):
                    self.check()
                self.ident[field] = original

    def test_unit_allow_cannot_replace_protection_parity(self):
        self.gate["units_at_parity"] = ["lsm2"]
        self.gate["board_overrides"] = {"j716s": "allow"}
        with self.assertRaisesRegex(ValueError, "policy refuses"):
            self.check()

    def test_deny_wins_over_existing_spp3_parity(self):
        self.gate["denied_boards"] = ["j716s"]
        with self.assertRaisesRegex(ValueError, "policy refuses"):
            self.check()

    def test_companion_refusals_and_lane_mismatch_remain_closed(self):
        original = copy.deepcopy(self.dump)
        for change in (lambda t: t.update(lsid={"kind": "lsid"}),
                       lambda t: t.update(refusals=[{"code": "unsupported"}]),
                       lambda t: t["protection"].update(lanes=list(range(4)))):
            self.dump = copy.deepcopy(original)
            change(self.dump["tuning"])
            with self.assertRaisesRegex(ValueError, "six-lane Spp3"):
                self.check()

    def test_missing_native_volume_does_not_admit_cubic_fallback(self):
        self.dump["tuning"]["volume_range"] = None
        with self.assertRaisesRegex(ValueError, "HAL volume range missing"):
            self.check()

    def test_invalid_volume_range_is_refused(self):
        for lo, hi in ((0, 0), (1, 2), (float("nan"), 0), (-60, float("inf"))):
            with self.subTest(lo=lo, hi=hi):
                self.dump["tuning"]["volume_range"].update(min_db=lo, max_db=hi)
                with self.assertRaisesRegex(ValueError, "HAL volume range invalid"):
                    self.check()


class RuntimeFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.package = self.base / "package"
        self.package.mkdir()
        self.root = self.base / "installed"
        self.root.mkdir()
        self.name = "usr/lib/lv2/sgv2.lv2/example.ttl"
        self.payload = self.package / "rootfs" / self.name
        self.payload.parent.mkdir(parents=True)
        self.payload.write_bytes(b"public test fixture\n")
        self.doc = {"schema": 1, "recipe": "j716s-guarded-speaker-runtime-v1", "source_commit": m.ref.REV,
                    "source_archive_sha256": m.ref.ARCHIVE_SHA, "transport_patch_sha256": m.ref.PATCH_SHA,
                    "native_release_patch_sha256": m.RELEASE_PATCH_SHA, "native_release_test_sha256": m.RELEASE_TEST_SHA,
                    "upgrade_unit_patch_sha256": m.UPGRADE_PATCH_SHA, "upgrade_unit_test_sha256": m.UPGRADE_TEST_SHA,
                    "upgrade_asset_patch_sha256": m.ASSET_PATCH_SHA, "upgrade_asset_test_sha256": m.ASSET_TEST_SHA,
                    "playback_authorized": False, "files": {self.name: m.ref.digest(self.payload)},
                    "modes": {self.name: 0o644}}
        self.publish()

    def publish(self):
        path = self.package / m.MANIFEST
        path.write_text(json.dumps(self.doc))
        self.sha = m.ref.digest(path)
        self.args = argparse.Namespace(package=self.package, package_sha256=self.sha, root=self.root)

    def test_fresh_install_and_exact_receipt_rollback(self):
        previous = os.umask(0o077)
        try:
            m.install(self.args)
        finally:
            os.umask(previous)
        target = self.root / self.name
        self.assertEqual(target.read_bytes(), self.payload.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o755)
        receipt = self.root / "var/lib/speakerguardd" / m.RECEIPT
        m.rollback(argparse.Namespace(root=self.root, receipt_sha256=m.ref.digest(receipt)))
        self.assertFalse(target.exists())
        self.assertFalse(receipt.exists())

    def test_existing_distribution_file_is_never_replaced(self):
        target = self.root / self.name
        target.parent.mkdir(parents=True)
        target.write_bytes(b"distribution owned")
        with self.assertRaisesRegex(ValueError, "existing runtime files"):
            m.install(self.args)
        self.assertEqual(target.read_bytes(), b"distribution owned")

    def test_mutated_package_fails_before_install(self):
        self.payload.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "payload changed"):
            m.install(self.args)
        self.assertFalse((self.root / self.name).exists())

    def test_unlisted_payload_is_not_installed(self):
        (self.payload.parent / "extra.ttl").write_bytes(b"unlisted")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            m.install(self.args)

    def test_changed_file_aborts_whole_rollback(self):
        m.install(self.args)
        receipt = self.root / "var/lib/speakerguardd" / m.RECEIPT
        target = self.root / self.name
        sha = m.ref.digest(receipt)
        target.write_bytes(b"user change")
        with self.assertRaisesRegex(ValueError, "installed file changed"):
            m.rollback(argparse.Namespace(root=self.root, receipt_sha256=sha))
        self.assertTrue(receipt.exists())
        self.assertEqual(target.read_bytes(), b"user change")

    def test_symlink_parent_cannot_redirect_root_install(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "usr").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            m.install(self.args)
        self.assertEqual(list(outside.iterdir()), [])

    def test_path_escape_or_unsafe_mode_is_not_runtime_payload(self):
        for name in ("../../escape", "/usr/bin/escape", "usr//bin/escape"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsafe runtime path"):
                m.safe_path(self.root, name)
        self.doc["modes"][self.name] = 0o4755
        self.publish()
        with self.assertRaisesRegex(ValueError, "payload changed"):
            m.install(self.args)


    def test_unbound_release_repair_cannot_be_installed(self):
        self.doc["native_release_patch_sha256"] = "0" * 64
        self.publish()
        with self.assertRaisesRegex(ValueError, "release-selection repair"):
            m.checked_package(self.package, self.sha)





if __name__ == "__main__":
    unittest.main()
