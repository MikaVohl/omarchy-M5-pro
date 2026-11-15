#!/usr/bin/env python3
"""Inspect PRIVATE exact-build camera ownership and vendor voice-routing evidence.

camera checks the externally anchored paired-macOS package against the current
Linux unit, then interprets only ISP/DART/privacy registry subtrees. bluetooth
inspects only the pinned bluetoothd ARM64e slice. Neither command opens a camera,
Bluetooth socket or audio device, sends HCI commands, or admits capture. Native
instruction/data addresses are analysis identities, never hardware addresses.
Outputs remain private under outputs/firmware; source binaries are not published.
"""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import struct

PROJECT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


camera = load("capture_camera", "prepare-camera-firmware.py")
bluetooth = load("capture_bluetooth", "prepare-bluetooth-native.py")
prores = load("capture_private_io", "prepare-prores-firmware.py")
SLICE_OFFSET, SLICE_SIZE = 0xc34000, 0xbe5d30
SLICE_SHA256 = "82e244d5dcafbc150e820416ac7037debc9def106eae043ee51ed4e2c84e7721"
BASE = 0x100000000
# Actual LC_FUNCTION_STARTS boundaries in the pinned ARM64e slice.
VOICE_SPANS = (
    ("ACI_WriteVoiceConfig", 0x125c0c, 0x348),
    ("ACI_completion_adapter", 0x1119b8, 0x8c),
    ("vendor_command_buffer", 0x211cc4, 0xa0),
    ("vendor_command_submit", 0x211d64, 0xec),
    ("ACI_dispatch_install", 0x114b18, 0x44),
    ("chipset_dispatch_select", 0x211b08, 0x1bc),
    ("WriteVoiceConfig_dispatch", 0x2170bc, 0x8c),
    ("route1_caller", 0x3ab8bc, 0x18),
    ("route0_caller", 0x3ab92c, 0x234),
    ("route2_caller", 0x3abe24, 0x2cc),
    ("route_completion", 0x3ab844, 0x58),
    ("route2_completion", 0x3abd1c, 0x58),
    ("interface_completion", 0x3abd74, 0x58),
    ("vendor_feature_support", 0x3e0ff8, 0x174),
    ("vendor_pending_clear", 0x3e2fb0, 0xc),
    ("vendor_completion_wait", 0x59ad30, 0x344),
    ("post_voice_dispatch_slot360", 0x217148, 0x98),
    ("interface_dispatch_slot378", 0x217294, 0x8c),
)
MAPPERS = (("mapper-isp-epipe0-exclave", 4), ("mapper-isp-epipe1-exclave", 5),
           ("mapper-isp-piodma0-exclave", 8), ("mapper-isp-piodma1-exclave", 9))


def registry_nodes(raw):
    if not raw or len(raw) > camera.MAX_INPUT:
        raise ValueError("Required camera registry subtree is empty or oversized")
    roots = plistlib.loads(raw)
    if not isinstance(roots, list):
        raise ValueError("Camera registry roots must be a list")
    pending = [(node, 0) for node in roots]
    nodes = []
    while pending:
        node, depth = pending.pop()
        if not isinstance(node, dict) or depth > 16 or len(nodes) >= 128:
            raise ValueError("Camera registry structure exceeds its bounds")
        children = node.get("IORegistryEntryChildren", [])
        if not isinstance(children, list) or len(children) > 128:
            raise ValueError("Invalid camera registry child list")
        nodes.append(node)
        pending.extend((child, depth + 1) for child in children)
    return nodes


def named(nodes, name):
    found = [node for node in nodes if node.get("IORegistryEntryName") == name]
    if len(found) != 1:
        raise ValueError("Required native registry object is missing or ambiguous: " + name)
    return found[0]


def camera_facts(files):
    isp = named(registry_nodes(files["ioreg/isp0-devicetree.plist"]), "isp0")
    dart_nodes = registry_nodes(files["ioreg/dart-isp0-devicetree.plist"])
    dart = named(dart_nodes, "dart-isp0")
    driver = named(registry_nodes(files["ioreg/isp0-service.plist"]), "AppleH16CamIn")
    if (isp.get("sensor-type") != struct.pack("<I", 118) or
            isp.get("cam-connections-scheme") != struct.pack("<I", 16) or
            isp.get("pre-loaded") != struct.pack("<I", 1) or "exclave-assigned" not in isp or
            isp.get("exclave-service") != b"com.apple.service.ISPEngine\0"):
        raise ValueError("Native ISP identity/ownership differs from exact J716s acquisition")
    if (dart.get("sid") != struct.pack("<2I", 0, 12) or
            dart.get("exclave-sid") != struct.pack("<4I", 4, 5, 8, 9) or
            dart.get("remap") != struct.pack("<5I", 1, 6, 7, 10, 11)):
        raise ValueError("Native ISP host/exclave stream split differs")
    for name, sid in MAPPERS:
        if named(dart_nodes, name).get("reg") != struct.pack("<I", sid):
            raise ValueError("Native exclave mapper identity differs")
    if (driver.get("CFBundleIdentifier") != "com.apple.driver.AppleH16CameraInterface" or
            driver.get("IOExclaveProxy") is not True or driver.get("FirmwareLoaded") is not True or
            driver.get("FrontCameraActive") is not False or driver.get("FrontCameraStreaming") is not False or
            driver.get("ISPFirmwareVersion") != "6.201" or
            driver.get("ISPFirmwareLinkDate") != "Oct 30 2026 - 02:10:26"):
        raise ValueError("Native camera driver state differs from the observed idle exclave proxy")
    segment_raw = isp.get("segment-ranges", b"")
    if len(segment_raw) != 64:
        raise ValueError("Native ISP requires exactly two complete preload segments")
    segments = [dict(zip(("phys", "iova", "remap", "size", "flags"), values))
                for values in struct.iter_unpack("<QQQII", segment_raw)]
    if [(s["iova"], s["size"]) for s in segments] != [(0, 0xc1c000), (0xc1c000, 0x13d0000)]:
        raise ValueError("Native ISP preload geometry differs")
    for segment in segments:
        if (not segment["phys"] or segment["phys"] & 0x3fff or
                segment["phys"] + segment["size"] >= 1 << 64 or
                segment["remap"] != (0x10000000000 | segment["iova"])):
            raise ValueError("Native ISP preload address/remap is invalid")
    if max(s["phys"] for s in segments) < min(s["phys"] + s["size"] for s in segments):
        raise ValueError("Native ISP preload physical segments overlap")
    pil = registry_nodes(files["ioreg/dynamic-pil.plist"])
    if (named(pil, "cil").get("state-source") != struct.pack("<I", 1) or
            named(pil, "mil").get("state-source") != struct.pack("<I", 2)):
        raise ValueError("Native privacy-indicator source identities differ")
    exbright = named(registry_nodes(files["ioreg/exbrightpil.plist"]), "exbrightpil")
    if ("exclave-assigned" not in exbright or
            exbright.get("exclave-service") != b"com.apple.service.EXBrightPILCore\0"):
        raise ValueError("Native privacy-indicator exclave identity differs")
    return {"platform_id": 118, "host_sids": [0, 12], "exclave_mappers": dict(MAPPERS),
            "remapped_sids": [1, 6, 7, 10, 11], "preload_segments": segments,
            "firmware_version": "6.201", "firmware_link_date": "Oct 30 2026 - 02:10:26",
            "driver_is_exclave_proxy": True, "camera_active": False, "camera_streaming": False,
            "indicator_service": "com.apple.service.EXBrightPILCore",
            "idle_is_not_quiescence_or_ownership_release": True,
            "sensor_identity_proven": False, "firmware_abi_qualified": False,
            "coprocessor_quiescence_verified": False, "privacy_indicator_qualified": False,
            "memory_handoff_qualified": False}


def branch_target(word, address):
    """Decode only ARM64 immediate B/BL; no register-call target inference."""
    if word & 0x7c000000 != 0x14000000:
        return None
    immediate = word & 0x3ffffff
    if immediate & (1 << 25):
        immediate -= 1 << 26
    return address + immediate * 4


def voice_image(raw):
    if prores.sha(raw) != bluetooth.SOURCE_HASHES["bluetoothd.macho"]:
        raise ValueError("Voice inspection requires exact pinned native bluetoothd")
    image = bytes(prores.span(raw, SLICE_OFFSET, SLICE_SIZE))
    if (prores.sha(image) != SLICE_SHA256 or
            struct.unpack_from("<3I", image) != (0xfeedfacf, 0x100000c, 0x80000002)):
        raise ValueError("Native voice inspection requires the exact ARM64e slice")
    pointer = struct.unpack_from("<Q", image, 0xb37688)[0]
    if pointer != 0x8010000000125c0c:
        raise ValueError("Native voice dispatch chained pointer differs")
    if any(struct.unpack_from("<Q", image, offset)[0] for offset in (0xb37690, 0xb376a8, 0xb376b0)):
        raise ValueError("Native ACI unsupported prerequisite dispatch slots differ")
    if (struct.unpack_from("<Q", image, 0xb44918)[0] != 0x80090000003ab8bc or
            struct.unpack_from("<Q", image, 0xb441b0)[0] != 0x8009d68d003ab92c):
        raise ValueError("Native routing callback data identities differ")
    # Check all proved direct callers, including the unconditional tail call.
    callers = ((0x3ab8d0, 0x3ab8bc, 1), (0x3aba80, 0x3ab92c, 0), (0x3abf3c, 0x3abe24, 2))
    for call, _, _ in callers:
        if branch_target(struct.unpack_from("<I", image, call)[0], BASE + call) != BASE + 0x2170bc:
            raise ValueError("Native WriteVoiceConfig caller differs")
    return image, callers


def bluetooth_facts(raw):
    image, callers = voice_image(raw)
    if importlib.metadata.version("capstone") != "5.0.6":
        raise ValueError("Native voice inspection requires capstone==5.0.6")
    import capstone
    decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    methods = []
    for name, offset, size in VOICE_SPANS:
        code = bytes(prores.span(image, offset, size))
        instructions = [{"address": address, "mnemonic": mnemonic, "operands": operands}
                        for address, width, mnemonic, operands in decoder.disasm_lite(code, BASE + offset)
                        if width == 4]
        if len(instructions) * 4 != size:
            raise ValueError("Native voice function does not completely disassemble")
        methods.append({"name": name, "slice_file_offset": offset, "address": BASE + offset,
                        "bytes": size, "sha256": prores.sha(code), "instructions": instructions})
    return {"source_sha256": bluetooth.SOURCE_HASHES["bluetoothd.macho"],
            "arm64e_slice": {"file_offset": SLICE_OFFSET, "bytes": SLICE_SIZE, "sha256": SLICE_SHA256},
            "dispatch": {"source_address": BASE + 0xb37688, "chained_pointer": 0x8010000000125c0c,
                         "table_source_address": BASE + 0xb37330, "table_bytes": 0xb68,
                         "installed_table_address": BASE + 0xbb4060, "voice_slot_offset": 0x358,
                         "installed_voice_slot_address": BASE + 0xbb43b8,
                         "wrapper_address": BASE + 0x2170bc},
            "vendor_opcode": 0xfe10, "native_payload_semantics": ["route == 1", 0, 255, 255, 255, 255],
            "observed_callers": [{"call_address": BASE + call, "function_address": BASE + function,
                                  "route_argument": route} for call, function, route in callers],
            "indirect_caller_data": {"route1": {"address": BASE + 0xb44918, "chained_pointer": 0x80090000003ab8bc},
                                     "route0": {"address": BASE + 0xb441b0, "chained_pointer": 0x8009d68d003ab92c}},
            "route0_feature_checks": ["BD_VSC_WRITE_PCM_PARAM", "BD_VSC_SET_SCO_PATH"],
            "route2_feature_checks": ["BD_VSC_WRITE_PCM_PARAM", "BD_VSC_WRITE_I2SPCM_INTF",
                                      "BD_VSC_DYN_VOICE_ROUTE_CHANGE"],
            "null_aci_prerequisite_slots": {"route0_first_dispatch": 0x360,
                                            "route2_interface_dispatch": 0x378,
                                            "route2_dynamic_dispatch": 0x380},
            "null_slot_does_not_authorize_another_route": True,
            "route0_sequence": "dispatch slot +0x360 with w1=5, await completion, WriteVoiceConfig(0), await completion",
            "route2_sequence": "WriteVoiceConfig(2), await completion, dispatch +0x378, await completion, dispatch +0x380, await completion",
            "routing_choice_qualified": False, "controller_readiness_qualified": False,
            "standard_sco_support_qualified": False, "private_hdr_supported": False,
            "remaining_contract": ["T6050 controller capability/state initialization and which native routing branch applies to host HCI SCO",
                                   "complete selected-branch prerequisite commands and successful completion/readiness evidence",
                                   "HDR negotiation, codec and four-byte transport header ABI (unsupported; never substitute for standard SCO)"],
            "native_methods": methods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cam = commands.add_parser("camera")
    cam.add_argument("package", type=Path)
    cam.add_argument("--manifest-sha256", required=True)
    bt = commands.add_parser("bluetooth")
    bt.add_argument("--bluetoothd", type=Path, required=True)
    for command in (cam, bt):
        command.add_argument("--output", type=Path, required=True, help="new private JSON under outputs/firmware")
    args = parser.parse_args()
    try:
        output = Path(os.path.abspath(args.output))
        if not output.is_relative_to(PROJECT / "outputs/firmware"):
            raise ValueError("Native capture inspection output must remain under outputs/firmware")
        if args.command == "camera":
            camera.check_macos(args.package, args.manifest_sha256)
            files = {name: camera.read_regular(args.package / name, private=True, allow_empty=True)
                     for name, _ in camera.MACOS_QUERIES}
            facts = camera_facts(files)
            binding = {"camera_manifest_sha256": args.manifest_sha256, **camera.current_identity()}
        else:
            facts = bluetooth_facts(prores.private_input(args.bluetoothd, bluetooth.MAX_EXECUTABLE))
            binding = {"source_unit_identity_verified": False,
                       "source_binding": "exact-build-static-protocol-bytes; not a unit runtime observation"}
        report = {"schema": 1, "kind": "native-capture-contract-inspection", "engine": args.command,
                  "macos_build": camera.BUILD, **binding, "facts": facts,
                  "capture_authorized": False, "hardware_touched": False}
        raw = (json.dumps(report, indent=2) + "\n").encode()
        parent, name = prores.open_parent(output)
        try:
            prores.write_new(parent, name, raw)
            os.fsync(parent)
        finally:
            os.close(parent)
        print(json.dumps({"report_sha256": prores.sha(raw), "capture_authorized": False, "hardware_touched": False}))
    except (OSError, ValueError, KeyError, struct.error, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(1, "Native capture inspection refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()
