"""Isolated one-shot marker durability/cancellation regressions; no hardware."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sep_trial", PROJECT / "scripts/stage-sep-probe.py")
sep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sep)


class SepTrialMarkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / "aura-try"
        self.old = b"spent\naurora/open-wifi.bin\n"
        self.marker.write_bytes(self.old)
        self.default = self.root / "boot.bin"
        self.default.write_bytes(b"preserved working bringup default")
        self.default_bytes = self.default.read_bytes()

    def update(self, readback=None, after_write=None, sync=None):
        sep.write_marker_checked(self.marker, self.old, sep.ARMED_MARKER,
                                 readback or self.marker.read_bytes, sync or (lambda: None), after_write)

    def test_arming_updates_only_existing_marker_and_preserves_default(self):
        inode = self.marker.stat().st_ino
        self.update()
        self.assertEqual(self.marker.read_bytes(), sep.ARMED_MARKER)
        self.assertEqual(self.marker.stat().st_ino, inode)
        self.assertEqual(self.default.read_bytes(), self.default_bytes)

    def test_stale_direct_readback_restores_original_marker(self):
        with self.assertRaisesRegex(RuntimeError, "direct-disk readback differs"):
            self.update(readback=lambda: self.old)
        self.assertEqual(self.marker.read_bytes(), self.old)
        self.assertEqual(self.default.read_bytes(), self.default_bytes)

    def test_concurrent_default_change_disarms_without_overwriting_other_work(self):
        def reject():
            self.default.write_bytes(b"concurrent selection")
            raise RuntimeError("protected selection changed")
        with self.assertRaisesRegex(RuntimeError, "protected selection changed"):
            self.update(after_write=reject)
        self.assertEqual(self.marker.read_bytes(), self.old)
        self.assertEqual(self.default.read_bytes(), b"concurrent selection")

    def test_interrupted_validation_restores_marker(self):
        def interrupt():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.update(after_write=interrupt)
        self.assertEqual(self.marker.read_bytes(), self.old)

    def test_changed_marker_is_not_overwritten(self):
        other = b"armed\naurora/other.bin\n"
        self.marker.write_bytes(other)
        with self.assertRaisesRegex(RuntimeError, "changed before update"):
            self.update()
        self.assertEqual(self.marker.read_bytes(), other)
        self.assertEqual(self.default.read_bytes(), self.default_bytes)

    def test_symlink_marker_cannot_modify_another_file(self):
        self.marker.unlink()
        self.marker.symlink_to(self.default)
        with self.assertRaises(ValueError):
            self.update()
        self.assertEqual(self.default.read_bytes(), self.default_bytes)

    def test_first_flush_failure_restores_old_bytes(self):
        with patch.object(sep.os, "fsync", side_effect=[OSError("flush failed"), None]):
            with self.assertRaisesRegex(OSError, "flush failed"):
                self.update()
        self.assertEqual(self.marker.read_bytes(), self.old)

    def test_restoration_flush_failure_is_not_silenced(self):
        def reject():
            raise RuntimeError("validation failed")
        with patch.object(sep.os, "fsync", side_effect=[None, OSError("restoration flush failed")]):
            with self.assertRaisesRegex(OSError, "restoration flush failed"):
                self.update(after_write=reject)

    def test_cancel_does_not_need_candidate_modules_or_receipt(self):
        self.marker.write_bytes(sep.ARMED_MARKER)
        with patch.object(sep, "ESP", self.root), \
             patch.object(sep, "preflight", return_value=({}, {}, "fixture-device")), \
             patch.object(sep.trial, "marker_location", side_effect=lambda unused: self.marker.read_bytes()), \
             patch.object(sep.subprocess, "run"):
            sep.cancel()
        self.assertEqual(self.marker.read_bytes(), sep.SPENT_MARKER)
        self.assertEqual(self.default.read_bytes(), self.default_bytes)
        self.assertFalse((self.root / "sep-probe.json").exists())


if __name__ == "__main__":
    unittest.main()
