"""Exercise actual atomic boot selection and recovery against an isolated ESP tree."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("persistent_selector", PROJECT / "scripts/select-open-wifi.py")
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PersistentSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.baseline = b"preserved baseline payload\0\xff\n"
        self.candidate = b"physically tested open-WiFi payload\0\xfe\n" * 257
        self.rescue = b"preserved rescue payload\0\xfd\n"
        self.deployment = {"root_payload_sha256": sha(self.baseline),
                           "rescue_sha256": sha(self.rescue), "payload_files": []}
        self.put("boot/aurora/root.bin", self.baseline)
        self.put("boot/aurora/rescue.bin", self.rescue)
        self.selected = self.put("boot/aurora/boot.bin", self.baseline)
        self.candidate_path = self.put("boot/aurora/open-wifi.bin", self.candidate)
        for tree in ("root", "rescue"):
            for name in ("Image.gz", "t6050-j716s.dtb", "initramfs.cpio.gz"):
                relative = "aurora/" + tree + "/" + name
                data = ("preserved dependency " + relative).encode()
                self.put("boot/" + relative, data)
                self.deployment["payload_files"].append({"path": relative, "size": len(data), "sha256": sha(data)})
        self.marker = self.put("boot/aura-try", b"spent\naurora/open-wifi.bin\n")
        self.module = self.put("usr/lib/modules/" + selector.RELEASE + "/kernel/brcmfmac.ko", b"candidate module")
        self.receipt = self.put("etc/j716s/open-wifi.json", b"trial receipt left intact")
        self.default_receipt = self.put("etc/j716s/open-wifi-default.json", b"persistent receipt left intact")
        self.helper = self.put("usr/local/lib/j716s/candidate_boot.py", b"upgraded helper left intact")
        self.guard = self.put("usr/local/lib/j716s/j716s-bind-boot.py", b"upgraded guard left intact")
        self.protected = {path: path.read_bytes() for path in self.root.rglob("*")
                          if path.is_file() and path != self.selected}

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def publish(self, after_publish=None, expected_sha256=None):
        selector.publish_selection(self.root, self.deployment,
                                   sha(self.candidate) if expected_sha256 is None else expected_sha256,
                                   after_publish)

    def assert_preserved(self):
        for path, expected in self.protected.items():
            with self.subTest(path=path.relative_to(self.root)):
                self.assertEqual(path.read_bytes(), expected)

    def assert_baseline(self):
        self.assertEqual(self.selected.read_bytes(), self.baseline)
        self.assertEqual(selector.digest(self.selected), self.deployment["root_payload_sha256"])

    def test_publication_copies_exact_candidate_and_preserves_every_other_file(self):
        self.publish()
        self.assertEqual(self.selected.read_bytes(), self.candidate)
        self.assertEqual(selector.digest(self.selected), sha(self.candidate))
        self.assert_preserved()
        self.assertEqual({path.name for path in self.selected.parent.iterdir()},
                         {"root.bin", "rescue.bin", "boot.bin", "open-wifi.bin", "root", "rescue"})

    def test_publication_is_atomic_and_not_a_hardlink(self):
        with self.selected.open("rb") as previous:
            self.publish()
            self.assertEqual(previous.read(), self.baseline)
        self.assertNotEqual(self.selected.stat().st_ino, self.candidate_path.stat().st_ino)
        self.candidate_path.write_bytes(b"later candidate corruption")
        self.assertEqual(self.selected.read_bytes(), self.candidate)

    def test_readback_callback_runs_after_publication(self):
        observed = []

        def inspect():
            observed.append(self.selected.read_bytes())
            self.assert_preserved()

        self.publish(inspect)
        self.assertEqual(observed, [self.candidate])

    def test_failed_postpublication_check_restores_baseline_and_propagates(self):
        def reject():
            self.assertEqual(self.selected.read_bytes(), self.candidate)
            raise RuntimeError("installed guard rejected selection")

        with self.assertRaisesRegex(RuntimeError, "installed guard rejected selection"):
            self.publish(reject)
        self.assert_baseline()
        self.assert_preserved()

    def test_interrupted_postpublication_check_restores_baseline(self):
        def interrupt():
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.publish(interrupt)
        self.assert_baseline()
        self.assert_preserved()

    def test_candidate_digest_mismatch_never_selects_unverified_bytes(self):
        self.candidate_path.write_bytes(b"tampered candidate")
        with self.assertRaisesRegex(ValueError, "payload changed"):
            self.publish()
        self.assert_baseline()
        self.assertEqual(self.candidate_path.read_bytes(), b"tampered candidate")

    def test_candidate_symlink_cannot_be_published(self):
        self.candidate_path.unlink()
        self.candidate_path.symlink_to("root.bin")
        with self.assertRaisesRegex(ValueError, "unsafe file"):
            self.publish(expected_sha256=sha(self.baseline))
        self.assert_baseline()
        self.assertTrue(self.candidate_path.is_symlink())

    def test_each_baseline_digest_is_required_before_publication(self):
        paths = [self.root / "boot/aurora/root.bin", self.root / "boot/aurora/rescue.bin"]
        paths += [self.root / "boot" / entry["path"] for entry in self.deployment["payload_files"]]
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b"corrupt preserved baseline")
                with self.assertRaisesRegex(ValueError, "baseline"):
                    self.publish()
                self.assert_baseline()
                path.write_bytes(original)

    def test_incomplete_baseline_inventory_is_rejected(self):
        self.deployment["payload_files"].pop()
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.publish()
        self.assert_baseline()

    def test_rollback_restores_exact_root_bytes_and_keeps_runtime_upgrade(self):
        self.publish()
        selector.rollback_selection(self.root, self.deployment)
        self.assert_baseline()
        self.assert_preserved()
        selector.rollback_selection(self.root, self.deployment)
        self.assert_baseline()

    def test_rollback_ignores_tampered_or_missing_candidate_dependencies(self):
        self.publish()
        self.candidate_path.write_bytes(b"damaged candidate payload")
        self.module.write_bytes(b"damaged candidate module")
        self.receipt.write_bytes(b"invalid candidate JSON")
        self.default_receipt.write_bytes(b"invalid persistent JSON")
        selector.rollback_selection(self.root, self.deployment)
        self.assert_baseline()
        self.assertEqual(self.module.read_bytes(), b"damaged candidate module")
        self.assertEqual(self.receipt.read_bytes(), b"invalid candidate JSON")
        self.assertEqual(self.default_receipt.read_bytes(), b"invalid persistent JSON")
        self.assertEqual(self.helper.read_bytes(), self.protected[self.helper])
        self.assertEqual(self.guard.read_bytes(), self.protected[self.guard])
        self.candidate_path.unlink()
        self.module.unlink()
        self.receipt.unlink()
        self.default_receipt.unlink()
        self.selected.write_bytes(self.candidate)
        selector.rollback_selection(self.root, self.deployment)
        self.assert_baseline()
        self.assertEqual(self.marker.read_bytes(), self.protected[self.marker])

    def test_rollback_rejects_bad_preserved_payload_without_changing_selection(self):
        self.publish()
        (self.root / "boot/aurora/root.bin").write_bytes(b"damaged baseline")
        with self.assertRaisesRegex(ValueError, "baseline"):
            selector.rollback_selection(self.root, self.deployment)
        self.assertEqual(self.selected.read_bytes(), self.candidate)

    def test_rollback_cannot_overwrite_a_symlink_destination(self):
        victim = self.put("unrelated", b"unrelated bytes")
        self.selected.unlink()
        self.selected.symlink_to(victim)
        with self.assertRaisesRegex(ValueError, "destination"):
            selector.rollback_selection(self.root, self.deployment)
        self.assertEqual(victim.read_bytes(), b"unrelated bytes")

    def test_rollback_failure_is_not_silenced(self):
        def corrupt_baseline_and_fail():
            (self.root / "boot/aurora/root.bin").write_bytes(b"baseline damaged after publication")
            raise RuntimeError("guard failure")

        with self.assertRaisesRegex(RuntimeError, "publication failed.*guard failure.*restoration also failed"):
            self.publish(corrupt_baseline_and_fail)
        self.assertEqual(self.selected.read_bytes(), self.candidate)


if __name__ == "__main__":
    unittest.main()
