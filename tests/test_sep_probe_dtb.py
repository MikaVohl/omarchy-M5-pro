"""KIS changes select exact sibling names and never mutate the wrong soc node."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sep_probe_dtb", PROJECT / "scripts/prepare-sep-probe-dtb.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def fixture_dtb(omit=None, disabled=()):
    strings = bytearray()
    offsets = {}

    def word(value):
        return struct.pack(">I", value)

    def padded(value):
        return value + b"\0" * (-len(value) % 4)

    def node(name, properties, children=()):
        result = word(1) + padded(name.encode() + b"\0")
        for key, value in properties.items():
            if key not in offsets:
                offsets[key] = len(strings)
                strings.extend(key.encode() + b"\0")
            result += word(3) + word(len(value)) + word(offsets[key]) + padded(value)
        return result + b"".join(children) + word(2)

    real, decoys = [], []
    for address in sorted(helper.dtb.KIS_ADDRESSES):
        if address == omit:
            continue
        iommu = address not in (0x2a2280000, 0x382280000)
        name = ("iommu@" if iommu else "usb@") + format(address, "x")
        compatible = b"apple,t6050-dart\0" if iommu else (
            b"snps,dwc3\0" if address == 0x2a2280000 else b"apple,t6050-dwc3\0")
        properties = {"reg": struct.pack(">QQ", address, 0x4000),
                      "compatible": compatible, "status": b"disabled\0" if address in disabled else b"okay\0"}
        real.append(node(name, properties))
        decoys.append(node(name, dict(properties, reg=struct.pack(">QQ", address + 0x10000000000, 0x4000))))
    tree = node("", {}, [node("soc@200000000", {}, decoys), node("soc", {}, real)]) + word(9)
    header = struct.pack(">10I", 0xd00dfeed, 56 + len(tree) + len(strings), 56,
                         56 + len(tree), 40, 17, 16, 0, len(strings), len(tree))
    return header + b"\0" * 16 + tree + strings


@unittest.skipUnless(shutil.which("gcc") and (PROJECT / "work/sources/aura/src/libfdt").is_dir(),
                     "requires the prepared Aura libfdt source and native gcc")
class SepProbeDtbTests(unittest.TestCase):
    def test_only_exact_unitless_soc_devices_take_baseline_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "probe.dtb"
            path.write_bytes(fixture_dtb())
            baseline_path = path.with_name("baseline.dtb")
            baseline_path.write_bytes(fixture_dtb(disabled=(0x2a2280000, 0x2a2f00000, 0x2a2f80000)))
            baseline = helper.dtb.dtb_nodes(baseline_path.read_bytes())
            before = helper.dtb.dtb_nodes(path.read_bytes())
            helper.prepare(path, baseline_path)
            after = helper.dtb.dtb_nodes(path.read_bytes())
            expected = {name: dict(properties) for name, properties in before.items()}
            for name in helper.dtb.kis_targets(before):
                expected[name]["status"] = baseline[name]["status"]
            self.assertEqual(after, expected)
            for name, properties in after.items():
                if name.startswith("/soc@200000000/"):
                    self.assertEqual(properties["status"], b"okay\0")

    def test_missing_kis_device_refuses_without_writing_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "probe.dtb"
            original = fixture_dtb(omit=0x2a2f00000)
            path.write_bytes(original)
            baseline_path = path.with_name("baseline.dtb")
            baseline_path.write_bytes(fixture_dtb())
            with self.assertRaises(ValueError):
                helper.prepare(path, baseline_path)
            self.assertEqual(path.read_bytes(), original)

    def test_enabled_usb2_is_restored_after_partial_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "probe.dtb"
            path.write_bytes(fixture_dtb(disabled=helper.dtb.KIS_ADDRESSES))
            baseline_path = path.with_name("baseline.dtb")
            baseline_path.write_bytes(fixture_dtb(disabled=(0x2a2280000, 0x2a2f00000, 0x2a2f80000)))
            before = helper.dtb.dtb_nodes(path.read_bytes())
            baseline = helper.dtb.dtb_nodes(baseline_path.read_bytes())
            helper.prepare(path, baseline_path)
            expected = {name: dict(properties) for name, properties in before.items()}
            for name in helper.dtb.kis_targets(before):
                expected[name]["status"] = baseline[name]["status"]
            self.assertEqual(helper.dtb.dtb_nodes(path.read_bytes()), expected)


if __name__ == "__main__":
    unittest.main()
