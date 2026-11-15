"""Synthetic bundle roundtrip regressions; no Apple data, services or hardware."""
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurora_speaker_upgrade.asahi_firmware import audio_tuning as at


class MatrixAsset(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        selection = {
            at.DATABASE: plistlib.dumps({"AID901": {"Built-in Speaker": {}}}),
            "Config/AID901.plist": plistlib.dumps({"DSP Devices": {}}),
        }
        files = {"DSP/Graphs/builtin_speaker_out.dspg": b"synthetic graph"}
        ends = list(range(1, 65)) + [64 + ((j + 1) * 257 + 35) // 36 for j in range(36)]
        lattice = struct.pack("<100Q", *ends)
        bundle = at.tree(901, files, None, "26C5035r", selection=selection,
                         source_count=1, board="j716s", unit="a" * 64, lattice=lattice)
        self.matrix = bytes(range(80))  # structural fixture only, not thermal/DSP tuning
        self.meta = json.loads(bundle[at.MANIFEST])
        self.meta["buzzkill_quadrature"] = {
            "path": "buzzkill-quadrature.bin", "bytes": 80,
            "sha256": hashlib.sha256(self.matrix).hexdigest(),
            "version": 1, "format": "f32le-sos4-output-major", "sections": 4,
        }
        bundle["buzzkill-quadrature.bin"] = self.matrix
        for name, data in bundle.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def load(self):
        (self.root / at.MANIFEST).write_text(json.dumps(self.meta))
        collection = at.AudioTuningFWCollection.__new__(at.AudioTuningFWCollection)
        collection.fwfiles, collection.bundle = [], {}
        collection.update(str(self.root))
        return collection

    def test_declared_matrix_survives_verified_collection_and_fallback(self):
        loaded = self.load()
        self.assertEqual(loaded.bundle["buzzkill-quadrature.bin"], self.matrix)
        output = self.root / "fallback"
        self.assertTrue(loaded.write_fallback(str(output)))
        self.assertEqual((output / "audio-tuning/buzzkill-quadrature.bin").read_bytes(), self.matrix)
        self.assertEqual(json.loads(loaded.bundle[at.MANIFEST])["buzzkill_quadrature"], self.meta["buzzkill_quadrature"])

    def test_changed_or_truncated_matrix_is_not_published(self):
        for data in (bytes(80), self.matrix[:-1]):
            with self.subTest(bytes=len(data)):
                (self.root / "buzzkill-quadrature.bin").write_bytes(data)
                with self.assertRaises(ValueError):
                    self.load()

    def test_missing_declared_matrix_is_not_silently_dropped(self):
        (self.root / "buzzkill-quadrature.bin").unlink()
        with self.assertRaises(OSError):
            self.load()

    def test_unknown_format_or_asset_path_is_refused(self):
        original = dict(self.meta["buzzkill_quadrature"])
        for key, value in (("path", "../foreign.bin"), ("bytes", 81), ("version", 2),
                           ("format", "unknown"), ("sections", 5)):
            with self.subTest(key=key):
                self.meta["buzzkill_quadrature"] = {**original, key: value}
                with self.assertRaisesRegex(ValueError, "quadrature record"):
                    self.load()


if __name__ == "__main__":
    unittest.main()
