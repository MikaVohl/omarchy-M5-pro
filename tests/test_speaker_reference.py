"""Admission regressions for native speaker references; no hardware or Apple data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("speaker_reference", Path(__file__).resolve().parents[1] / "scripts/capture-speaker-reference.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReferenceAdmission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {"unit_id": "a" * 64, "frames": 3, "speaker_id": 3}
        self.case = self.root / "refs/AID41-spid3"
        self.case.mkdir(parents=True)
        self.meta = {"aid": 41, "channels": 6, "frames": 3, "param_wires": []}
        (self.case / "unit_in.f32").write_bytes(bytes(3 * 6 * 4))
        (self.case / "status.json").write_text(json.dumps({"rc": 0}))
        self.doc = {"schema": 1, "recipe": "j716s-native-voicing-result-v1",
                    "request_sha256": "b" * 64, "unit_id": "a" * 64,
                    "macos_build": m.BUILD, "audiodsp_sha256": m.DSP_SHA,
                    "case": "AID41-spid3", "audio_device_opened": False}

    def publish(self):
        (self.case / "meta.json").write_text(json.dumps(self.meta))
        self.doc["files"] = m.inventory(self.root)
        path = self.root / m.RESULT
        path.write_text(json.dumps(self.doc))
        return m.digest(path)

    def check(self):
        return m.checked_result(self.request, "b" * 64, self.root, self.publish())

    def test_foreign_unit_cannot_supply_native_parity(self):
        self.doc["unit_id"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "identity/provenance"):
            self.check()

    def test_older_release_is_not_native_current_build(self):
        self.doc["macos_build"] = "25G83"
        with self.assertRaisesRegex(ValueError, "identity/provenance"):
            self.check()

    def test_truncated_audio_is_not_successful_render(self):
        (self.case / "unit_in.f32").write_bytes(bytes(2 * 6 * 4))
        with self.assertRaisesRegex(ValueError, "frame count"):
            self.check()

    def test_modelled_missing_audiounit_cannot_be_reference(self):
        self.meta["param_wires"] = [{"modelled_box": "StereoPanning", "law": "saved matrix"}]
        with self.assertRaisesRegex(ValueError, "not a native reference"):
            self.check()

    def test_nonfinite_native_audio_cannot_establish_parity(self):
        import struct
        (self.case / "unit_in.f32").write_bytes(struct.pack("<f", float("nan")) + bytes(3 * 6 * 4 - 4))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.check()

    def test_wrong_vendor_case_cannot_escape_reference_inventory(self):
        self.doc["case"] = "../../other-unit"
        with self.assertRaisesRegex(ValueError, "case differs"):
            self.check()

    def test_mutated_reference_cannot_reuse_external_manifest(self):
        sha = self.publish()
        (self.case / "unit_in.f32").write_bytes(bytes(4 * 6 * 4))
        with self.assertRaisesRegex(ValueError, "input changed"):
            m.checked_result(self.request, "b" * 64, self.root, sha)

    def test_symlink_cannot_import_unmanifested_reference(self):
        sha = self.publish()
        target = self.root / "outside"
        target.write_bytes(bytes(3 * 6 * 4))
        (self.case / "unit_in.f32").unlink()
        (self.case / "unit_in.f32").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "input changed"):
            m.checked_result(self.request, "b" * 64, self.root, sha)


if __name__ == "__main__":
    unittest.main()
