"""Offline camera provenance/admission regressions; no camera or MMIO access."""
import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("camera_preparation", ROOT / "scripts/prepare-camera-firmware.py")
camera = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera)
KERNEL_SOURCE = ROOT / "work/sources/kernel"
NEEDS_KERNEL_SOURCE = unittest.skipUnless(
    (KERNEL_SOURCE / "tools/aurora-sep/aurora-adt-extract.py").is_file(),
    "needs prepared sources: python3 -B scripts/prepare-sources.py --fetch")


def node(name, properties=(), children=()):
    properties = [("name", name.encode() + b"\0"), *properties]
    result = struct.pack("<II", len(properties), len(children))
    for key, value in properties:
        result += key.encode().ljust(32, b"\0") + struct.pack("<I", len(value))
        result += value + b"\0" * (-len(value) % 4)
    return result + b"".join(children)


def adt(segments=None, platform_id=118, sids=(4, 5, 8, 9), duplicate=False):
    if segments is None:
        segments = ((0x100010cc000, 0, 0x10000000000, 0xc1c000, 1),
                    (0x100026a4000, 0xc1c000, 0x10000c1c000, 0x13d0000, 0))
    properties = [("pre-loaded", struct.pack("<I", 1)), ("exclave-assigned", b""),
                  ("sensor-type", struct.pack("<I", platform_id)),
                  ("cam-connections-scheme", struct.pack("<I", 16)),
                  ("segment-ranges", b"".join(struct.pack("<QQQII", *seg) for seg in segments)),
                  ("serial-number", b"PRIVATE-CAMERA-SERIAL")]
    if duplicate:
        properties.append(("sensor-type", struct.pack("<I", platform_id)))
    return node("device-tree", children=[node("arm-io", children=[
        node("isp0", properties), node("dart-isp0", [("exclave-sid", struct.pack("<" + "I" * len(sids), *sids))])])])


def daemon_fixture():
    blob = bytearray(camera.PADDED_SIZE)
    struct.pack_into(">8I", blob, 0, camera.SETFILE_MAGIC, 0x1a560000, 0, 0, 0x20000000, 0x644b0000, 0, 0x3c00000)
    blob[64:80] = b"TEST-TUNING-DATA"
    daemon = b"\0" * 0x1000 + bytes(blob) + b"\0" * 0x1000 + bytes(blob)
    return daemon, bytes(blob), [0x1000, 0x9000]


@NEEDS_KERNEL_SOURCE
class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = camera.parser_module(ROOT / "work/sources/kernel")

    def test_current_addresses_are_derived_not_old_static_ranges(self):
        report = camera.handoff(adt(), self.parser)
        self.assertEqual(report["segments"][0]["phys"], 0x100010cc000)
        self.assertEqual(report["segments"][1]["phys"], 0x100026a4000)
        self.assertEqual(report["exclave_sids"], [4, 5, 8, 9])
        self.assertFalse(report["sensor_identity_proven"])
        self.assertFalse(report["coprocessor_quiescence_verified"])
        self.assertFalse(report["firmware_abi_qualified"])
        self.assertNotIn(b"PRIVATE-CAMERA-SERIAL", camera.encoded(report))

    def test_platform_id_is_not_sensor_identity(self):
        with self.assertRaisesRegex(ValueError, "platform metadata"):
            camera.handoff(adt(platform_id=0x958), self.parser)
        with self.assertRaisesRegex(ValueError, "ownership differs"):
            camera.handoff(adt(sids=(0, 4, 5, 8, 9)), self.parser)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            camera.handoff(adt(duplicate=True), self.parser)

    def test_segment_bounds_alignment_overlap_and_layout(self):
        text = (0x100010cc000, 0, 0x10000000000, 0xc1c000, 1)
        data = (0x100026a4000, 0xc1c000, 0x10000c1c000, 0x13d0000, 0)
        cases = [(text,), (text, (*data[:3], 0x13c0000, 0)),
                 ((*text[:3], 0xc1c001, 1), data),
                 (text, (text[0], *data[1:])),
                 (text, (data[0], data[1], 0, data[3], data[4])),
                 ((0xffffffffffffc000, *text[1:]), data)]
        for segments in cases:
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                camera.handoff(adt(segments=segments), self.parser)


class SetfileTests(unittest.TestCase):
    def test_exact_duplicates_are_preserved_byte_for_byte(self):
        daemon, blob, offsets = daemon_fixture()
        with patch.object(camera, "DAEMON_SHA256", camera.sha(daemon)):
            extracted, actual_offsets = camera.extract_setfile(daemon)
        self.assertEqual(extracted, blob)
        self.assertEqual(actual_offsets, offsets)
        self.assertEqual(len(extracted), camera.PADDED_SIZE)

    def test_source_hash_rejects_other_builds_before_extraction(self):
        daemon, _, _ = daemon_fixture()
        with self.assertRaisesRegex(ValueError, "observed exact 26C5035r"):
            camera.extract_setfile(daemon)

    def test_ambiguous_or_truncated_candidates_are_refused(self):
        daemon, _, offsets = daemon_fixture()
        changed = bytearray(daemon)
        changed[offsets[1] + 100] ^= 1
        for data in (bytes(changed), daemon[:offsets[0] + 100], b"\0" * 4096):
            with patch.object(camera, "DAEMON_SHA256", camera.sha(data)), self.assertRaises(ValueError):
                camera.extract_setfile(data)


class PackageTests(unittest.TestCase):
    @NEEDS_KERNEL_SOURCE
    def test_anchor_source_evidence_boot_and_private_permissions(self):
        daemon, blob, offsets = daemon_fixture()
        raw = adt()
        parser = camera.parser_module(ROOT / "work/sources/kernel")
        identity = {"board": "j716s", "model": "Mac17,8", "chip": "t6050", "macos_build": camera.BUILD,
                    "unit_serial_sha256": "UNIT-HASH", "boot_id": "SAME-BOOT",
                    "firmware_binding": "exact-label-and-mboot-pair", "current_restore_build_verified": False}
        import plistlib
        version = plistlib.dumps({"ProductBuildVersion": camera.BUILD})
        report = {"schema": 1, **identity, "handoff": camera.handoff(raw, parser), "adt_sha256": camera.sha(raw),
                  "daemon_sha256": camera.sha(daemon), "system_version_sha256": camera.sha(version),
                  "firmware": camera.FIRMWARE, "firmware_sha256": camera.sha(blob), "firmware_bytes": len(blob),
                  "daemon_offsets": offsets, "installed": False, "sensor_identity_proven": False}
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            camera.save_new(package, {camera.FIRMWARE: blob, "source/appleh16camerad": daemon,
                                     "source/adt.bin": raw, "source/SystemVersion.plist": version,
                                     camera.REPORT: camera.encoded(report)})
            anchor = camera.sha(camera.encoded(report))
            with patch.object(camera, "DAEMON_SHA256", camera.sha(daemon)), patch.object(camera, "current_identity", return_value=identity):
                self.assertTrue(camera.check(package, anchor, ROOT / "work/sources/kernel")["checked"])
                with self.assertRaisesRegex(ValueError, "external SHA256"):
                    camera.check(package, "0" * 64, ROOT / "work/sources/kernel")
                with patch.object(camera, "current_identity", return_value={**identity, "boot_id": "LATER-BOOT"}):
                    with self.assertRaisesRegex(ValueError, "current boot"):
                        camera.check(package, anchor, ROOT / "work/sources/kernel")
                tuning = package / camera.FIRMWARE
                tuning.write_bytes(b"altered")
                with self.assertRaisesRegex(ValueError, "candidate tuning differs"):
                    camera.check(package, anchor, ROOT / "work/sources/kernel")
                tuning.write_bytes(blob)
                tuning.chmod(0o644)
                with self.assertRaisesRegex(ValueError, "not private"):
                    camera.check(package, anchor, ROOT / "work/sources/kernel")


class MacOSAcquisitionTests(unittest.TestCase):
    def test_targeted_acquisition_checks_sources_unit_build_and_guards(self):
        daemon, blob, _ = daemon_fixture()
        platform_plist = plistlib.dumps([{"model": b"Mac17,8\0", "IOPlatformSerialNumber": "UNIT-A"}])
        identity = {"schema": 1, "board": "j716s", "model": "Mac17,8", "macos_build": camera.BUILD,
                    "unit_serial_sha256": camera.sha(b"UNIT-A"), "ioreg_platform_sha256": camera.sha(platform_plist)}
        version = plistlib.dumps({"ProductBuildVersion": camera.BUILD})
        sources = {"/System/Library/CoreServices/SystemVersion.plist": version, "/usr/sbin/appleh16camerad": daemon}
        als = camera.als_tool()
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "capture"
            with patch.object(camera, "als_tool", return_value=als), \
                 patch.object(als, "macos_identity", return_value=(identity, platform_plist)), \
                 patch.object(Path, "iterdir", return_value=iter(())), \
                 patch.object(camera, "read_regular", side_effect=lambda path: sources[str(path)]), \
                 patch.object(camera, "DAEMON_SHA256", camera.sha(daemon)), \
                 patch.object(camera.subprocess, "check_output", side_effect=[b"", *([plistlib.dumps([])] * 5)]) as command:
                result = camera.capture_macos(package, identity["unit_serial_sha256"])
            self.assertFalse(result["hardware_touched"])
            self.assertFalse(result["sensor_identity_proven"])
            self.assertEqual((package / camera.FIRMWARE).read_bytes(), blob)
            self.assertEqual(command.call_count, len(camera.MACOS_QUERIES))
            for call in command.call_args_list:
                self.assertEqual(call.args[0][:4], ["/usr/sbin/ioreg", "-r", "-a", "-l"])
            with patch.object(camera, "current_identity", return_value=identity), \
                 patch.object(camera, "DAEMON_SHA256", camera.sha(daemon)):
                self.assertTrue(camera.check_macos(package, result["manifest_sha256"])["checked"])
                with self.assertRaisesRegex(ValueError, "external SHA256"):
                    camera.check_macos(package, "0" * 64)
                with patch.object(camera, "current_identity", return_value={**identity, "unit_serial_sha256": "OTHER-UNIT"}):
                    with self.assertRaisesRegex(ValueError, "unit, build"):
                        camera.check_macos(package, result["manifest_sha256"])
                manifest = package / camera.MACOS_REPORT
                report = json.loads(manifest.read_bytes())
                report["privacy_indicator_qualified"] = True
                manifest.write_bytes(camera.encoded(report))
                with self.assertRaisesRegex(ValueError, "qualification claims"):
                    camera.check_macos(package, camera.sha(manifest.read_bytes()))
                report["privacy_indicator_qualified"] = False
                manifest.write_bytes(camera.encoded(report))
                (package / camera.FIRMWARE).write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "source bytes differ"):
                    camera.check_macos(package, camera.sha(manifest.read_bytes()))

    def test_wrong_paired_identity_prevents_all_camera_source_reads(self):
        als = camera.als_tool()
        with patch.object(camera, "als_tool", return_value=als), \
             patch.object(als, "macos_identity", side_effect=ValueError("different physical unit")), \
             patch.object(camera.subprocess, "check_output") as command, \
             patch.object(camera, "read_regular") as source:
            with self.assertRaisesRegex(ValueError, "different physical unit"):
                camera.capture_macos(Path("unused"), "0" * 64)
            command.assert_not_called()
            source.assert_not_called()

    def test_empty_registry_is_allowed_only_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.plist"
            path.write_bytes(b"")
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "bounded single-link"):
                camera.read_regular(path, private=True)
            self.assertEqual(camera.read_regular(path, private=True, allow_empty=True), b"")


if __name__ == "__main__":
    unittest.main()
