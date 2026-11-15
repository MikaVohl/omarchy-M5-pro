import importlib.util
from pathlib import Path
import struct
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/ane-27_3-proof.py"
SPEC = importlib.util.spec_from_file_location("ane_proof", MODULE_PATH)
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


class AneProofTests(unittest.TestCase):
    def test_native_stride_compares_all_sixty_four_lanes(self):
        data = proof.fixtures()
        self.assertEqual(proof.verify_result(data["expected.bin"]), 64)
        for lane in range(64):
            offset = lane * 64
            x = struct.unpack_from("<e", data["a.bin"], offset)[0]
            z = struct.unpack_from("<e", data["b.bin"], offset)[0]
            self.assertEqual(data["expected.bin"][offset:offset + 2], struct.pack("<e", x + z))

    def test_truncated_or_empty_comparison_cannot_pass(self):
        expected = proof.fixtures()["expected.bin"]
        for candidate in (b"", expected[:128], expected[:-2], expected + b"\0\0"):
            with self.subTest(size=len(candidate)), self.assertRaisesRegex(ValueError, "exactly"):
                proof.verify_result(candidate)

    def test_poison_noop_and_last_lane_corruption_fail(self):
        expected = proof.fixtures()["expected.bin"]
        corrupt = bytearray(expected)
        corrupt[63 * 64] ^= 1
        for candidate in (b"\xff" * proof.SURFACE_BYTES, b"\0" * proof.SURFACE_BYTES,
                          proof.fixtures()["a.bin"], bytes(corrupt)):
            with self.assertRaisesRegex(ValueError, "lane"):
                proof.verify_result(candidate)


if __name__ == "__main__":
    unittest.main()
