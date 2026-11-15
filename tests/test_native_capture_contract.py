"""Offline production camera/voice evidence guards; no device access or audio."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_capture", ROOT / "scripts/inspect-capture-native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def registry(name, children=(), **properties):
    return {"IORegistryEntryName": name, "IORegistryEntryChildren": list(children), **properties}


def acquisition_nodes():
    segments = struct.pack("<QQQIIQQQII", 0x1010cc000, 0, 0x10000000000, 0xc1c000, 1,
                           0x1026a4000, 0xc1c000, 0x10000c1c000, 0x13d0000, 0)
    isp = registry("isp0", **{"sensor-type": struct.pack("<I", 118),
                             "cam-connections-scheme": struct.pack("<I", 16),
                             "pre-loaded": struct.pack("<I", 1), "exclave-assigned": b"",
                             "exclave-service": b"com.apple.service.ISPEngine\0",
                             "segment-ranges": segments})
    dart = registry("dart-isp0", [registry(name, reg=struct.pack("<I", sid)) for name, sid in native.MAPPERS],
                    **{"sid": struct.pack("<2I", 0, 12), "exclave-sid": struct.pack("<4I", 4, 5, 8, 9),
                       "remap": struct.pack("<5I", 1, 6, 7, 10, 11)})
    driver = registry("AppleH16CamIn", CFBundleIdentifier="com.apple.driver.AppleH16CameraInterface",
                      IOExclaveProxy=True, FirmwareLoaded=True, FrontCameraActive=False,
                      FrontCameraStreaming=False, ISPFirmwareVersion="6.201",
                      ISPFirmwareLinkDate="Oct 30 2026 - 02:10:26")
    pil = registry("dynamic-pil", [registry("cil", **{"state-source": struct.pack("<I", 1)}),
                                   registry("mil", **{"state-source": struct.pack("<I", 2)})])
    exbright = registry("exbrightpil", **{"exclave-assigned": b"",
                                         "exclave-service": b"com.apple.service.EXBrightPILCore\0"})
    return {"ioreg/isp0-devicetree.plist": [isp], "ioreg/dart-isp0-devicetree.plist": [dart],
            "ioreg/isp0-service.plist": [registry("isp0", [driver])],
            "ioreg/dynamic-pil.plist": [pil], "ioreg/exbrightpil.plist": [exbright]}


def encoded(nodes):
    return {name: plistlib.dumps(tree) for name, tree in nodes.items()}


class CameraOwnershipTests(unittest.TestCase):
    def test_idle_exclave_proxy_never_grants_sensor_quiescence_or_privacy(self):
        facts = native.camera_facts(encoded(acquisition_nodes()))
        self.assertEqual(facts["host_sids"], [0, 12])
        self.assertEqual(set(facts["exclave_mappers"].values()), {4, 5, 8, 9})
        self.assertTrue(facts["idle_is_not_quiescence_or_ownership_release"])
        for key in ("sensor_identity_proven", "firmware_abi_qualified", "coprocessor_quiescence_verified",
                    "privacy_indicator_qualified", "memory_handoff_qualified"):
            self.assertIs(facts[key], False)

    def test_host_stream_list_cannot_claim_an_exclave_stream(self):
        nodes = acquisition_nodes()
        nodes["ioreg/dart-isp0-devicetree.plist"][0]["sid"] = struct.pack("<3I", 0, 4, 12)
        with self.assertRaisesRegex(ValueError, "stream split"):
            native.camera_facts(encoded(nodes))

    def test_missing_or_misassigned_exclave_mapper_refuses(self):
        for changed in (False, True):
            nodes = acquisition_nodes()
            children = nodes["ioreg/dart-isp0-devicetree.plist"][0]["IORegistryEntryChildren"]
            if changed:
                children[0]["reg"] = struct.pack("<I", 0)
            else:
                children.pop()
            with self.assertRaises(ValueError):
                native.camera_facts(encoded(nodes))

    def test_duplicate_driver_identity_refuses(self):
        nodes = acquisition_nodes()
        children = nodes["ioreg/isp0-service.plist"][0]["IORegistryEntryChildren"]
        children.append(copy.deepcopy(children[0]))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            native.camera_facts(encoded(nodes))

    def test_active_or_unknown_firmware_state_is_not_idle_evidence(self):
        for property_name, value in (("FrontCameraStreaming", True), ("FrontCameraActive", 0),
                                     ("ISPFirmwareVersion", "6.200"), ("IOExclaveProxy", False)):
            nodes = acquisition_nodes()
            nodes["ioreg/isp0-service.plist"][0]["IORegistryEntryChildren"][0][property_name] = value
            with self.subTest(property_name=property_name), self.assertRaisesRegex(ValueError, "driver state"):
                native.camera_facts(encoded(nodes))

    def test_preload_overlap_and_remap_mismatch_refuse(self):
        for overlap in (False, True):
            nodes = acquisition_nodes()
            isp = nodes["ioreg/isp0-devicetree.plist"][0]
            raw = bytearray(isp["segment-ranges"])
            if overlap:
                struct.pack_into("<Q", raw, 32, 0x1010cc000)
            else:
                struct.pack_into("<Q", raw, 16, 0)
            isp["segment-ranges"] = bytes(raw)
            with self.assertRaises(ValueError):
                native.camera_facts(encoded(nodes))

    def test_privacy_service_name_alone_does_not_qualify_an_indicator(self):
        nodes = acquisition_nodes()
        del nodes["ioreg/exbrightpil.plist"][0]["exclave-assigned"]
        with self.assertRaisesRegex(ValueError, "privacy-indicator"):
            native.camera_facts(encoded(nodes))

    def test_deep_and_oversized_registry_structures_refuse(self):
        root = registry("root")
        for _ in range(18):
            root = registry("parent", [root])
        for roots in ([root], [registry(str(index)) for index in range(129)]):
            with self.assertRaisesRegex(ValueError, "bounds"):
                native.registry_nodes(plistlib.dumps(roots))


class VoiceEvidenceTests(unittest.TestCase):
    def test_direct_branch_targets_preserve_signed_displacements(self):
        self.assertEqual(native.branch_target(0x94000004, 0x1000), 0x1010)
        self.assertEqual(native.branch_target(0x17ffffff, 0x1000), 0xffc)
        self.assertIsNone(native.branch_target(0xd63f0100, 0x1000))
        self.assertIsNone(native.branch_target(0x54000080, 0x1000))

    def test_wrong_slice_or_binary_refuses_before_disassembler_loading(self):
        for raw in (b"", b"\xcf\xfa\xed\xfe" + bytes(64), b"\xca\xfe\xba\xbe" + bytes(64)):
            with self.assertRaisesRegex(ValueError, "exact pinned"):
                native.voice_image(raw)

    def test_unanchored_retained_unit_evidence_refuses_before_live_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "platform.plist"
            data = plistlib.dumps([{"model": b"Mac17,8\0", "IOPlatformSerialNumber": "PRIVATE-UNIT"}])
            path.write_bytes(data)
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(ValueError, "external SHA256"):
                native.bluetooth.bind_unit((path, hashlib.sha256(data + b"changed").hexdigest()), "a" * 64)


if __name__ == "__main__":
    unittest.main()
