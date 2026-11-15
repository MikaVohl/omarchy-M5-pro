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
        self.meta = {"aid": 41, "channels": 6, "frames": 3, "param_wires": [],
                     "params": {"mvsc": 1.0, "mvol": 0.0, "mmut": 0.0}}
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

    def test_lower_volume_is_not_full_volume_reference(self):
        self.meta["params"]["mvsc"] = 0.5
        with self.assertRaisesRegex(ValueError, "full-volume"):
            self.check()

    def test_muted_native_output_is_not_parity_reference(self):
        self.meta["params"]["mmut"] = 1.0
        with self.assertRaisesRegex(ValueError, "full-volume"):
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


class ParityAdmission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "parity.json"
        self.request = {"key": "a" * 64, "law": "b" * 64, "speaker_id": 3}
        self.entry = {"key": "a" * 64, "aid": 41, "speaker_id": 3, "also_speaker_ids": [],
                      "verdict": "pass", "measured": 182, "worst_louder_db": 0.184, "worst_quieter_db": -0.048}
        self.doc = {"format": 2, "law": "b" * 64,
                    "tolerance": {"louder_db": 0.25, "quieter_db": 1.0, "floor_residue_db": 55.0},
                    "releases": [{"build": m.BUILD, "rev": m.REV, "entries": [self.entry]}]}

    def publish(self):
        self.path.write_text(json.dumps(self.doc))
        return m.digest(self.path)

    def test_exact_externally_anchored_native_gate_is_admitted(self):
        self.assertEqual(m.checked_parity(self.request, self.path, self.publish()), self.doc)

    def test_other_build_or_law_cannot_admit_tuning(self):
        for field, value in (("law", "c" * 64), ("format", 1)):
            with self.subTest(field=field):
                original = self.doc[field]
                self.doc[field] = value
                with self.assertRaisesRegex(ValueError, "format/law/tolerance"):
                    m.checked_parity(self.request, self.path, self.publish())
                self.doc[field] = original
        self.doc["releases"][0]["build"] = "25G83"
        with self.assertRaisesRegex(ValueError, "release/revision"):
            m.checked_parity(self.request, self.path, self.publish())

    def test_passing_label_does_not_override_failed_measurement(self):
        for field, value in (("worst_louder_db", 0.251), ("worst_quieter_db", -1.001),
                             ("worst_louder_db", float("nan")), ("measured", 0), ("measured", True)):
            with self.subTest(field=field, value=value):
                original = self.entry[field]
                self.entry[field] = value
                with self.assertRaisesRegex(ValueError, "measurements"):
                    m.checked_parity(self.request, self.path, self.publish())
                self.entry[field] = original

    def test_other_vendor_or_key_cannot_reuse_passing_reference(self):
        for field, value in (("speaker_id", 0), ("key", "c" * 64), ("also_speaker_ids", [0]), ("verdict", "refused")):
            with self.subTest(field=field):
                original = self.entry[field]
                self.entry[field] = value
                with self.assertRaisesRegex(ValueError, "identity/verdict"):
                    m.checked_parity(self.request, self.path, self.publish())
                self.entry[field] = original

    def test_mutated_parity_cannot_reuse_external_digest(self):
        sha = self.publish()
        self.path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "external parity"):
            m.checked_parity(self.request, self.path, sha)


if __name__ == "__main__":
    unittest.main()
