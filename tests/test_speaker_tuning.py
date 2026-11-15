import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("speaker_tuning_prepare", Path(__file__).resolve().parents[1] / "scripts/prepare-speaker-tuning.py")
speaker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speaker)


class SpeakerBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dt = Path(self.temp.name)
        (self.dt / "chosen").mkdir()
        (self.dt / "compatible").write_bytes(b"apple,j716s\0apple,t6050\0")
        (self.dt / "serial-number").write_bytes(b"SYNTHETIC0A\0")
        (self.dt / "chosen/asahi,speaker-id").write_bytes((3).to_bytes(4, "big"))
        self.meta = {"schema": 2, "complete": True, "aid": "AID41", "acoustic_id": 41,
                     "board": "j716s", "macos_build": "26C5035r", "speaker_id": 3,
                     "unit_id": speaker.unit_id(self.dt),
                     "speaker_thiele_small": {"bytes": 138}, "lsm2_mask_lattice": {"bytes": 800}}

    def test_unit_hash_matches_upstream_binding(self):
        self.assertEqual(speaker.unit_id(self.dt), "22fea1b624a25cb3e103ed668fc37e1ef5c0d445e195029caf273827923c793d")
        speaker.admit_manifest(self.meta, self.dt)

    def test_foreign_or_unbound_factory_calibration_is_refused(self):
        for unit in (None, "0" * 64):
            with self.subTest(unit=unit):
                meta = dict(self.meta, unit_id=unit)
                with self.assertRaisesRegex(ValueError, "another or unidentified unit"):
                    speaker.admit_manifest(meta, self.dt)

    def test_changed_vendor_build_or_missing_calibration_is_refused(self):
        for field, value in (("speaker_id", 0), ("macos_build", "26C9999"),
                             ("complete", False), ("speaker_thiele_small", None)):
            with self.subTest(field=field):
                meta = copy.deepcopy(self.meta)
                meta[field] = value
                with self.assertRaises(ValueError):
                    speaker.admit_manifest(meta, self.dt)

    def test_writable_system_is_rejected_before_reading_files(self):
        result = json.dumps({"filesystems": [{"target": "/synthetic", "options": "rw,nosuid"}]})
        with patch.object(speaker.subprocess, "check_output", return_value=result):
            with self.assertRaisesRegex(ValueError, "read-only"):
                speaker.readonly_system(self.dt / "nonexistent")


if __name__ == "__main__":
    unittest.main()
