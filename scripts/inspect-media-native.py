#!/usr/bin/env python3
"""Inspect source-pinned 26C5035r media boot/ownership and command-size evidence.

Requires the same private retained kernelcache and lzfse==0.4.2 as ProRes
preparation, plus capstone==5.0.6. Never accesses hardware or installs firmware.
Writes private native disassembly, not an enablement recipe or a runtime pass.
No user-selected register maps, instruction execution, patched input or foreign
firmware is admitted. The complete decoded-image hash binds every fixed span.
"""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import struct
import zlib

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("inspected_prores", PROJECT / "scripts/prepare-prores-firmware.py")
prores = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prores)

# Observed FILE spans in the exact pinned collection, NOT MMIO addresses.
# Function boundaries were matched to LC_SYMTAB in their own fileset entries.
SPANS = (
    ("prores", "WriteRegister32", 0x2979644, 0xb0),
    ("prores", "hangRecovery", 0x29796f4, 0x1cc),
    ("prores", "ReadRegister32", 0x29798c0, 0xa4),
    ("prores", "quiesceHWBulkTraffic", 0x2979964, 0x498),
    ("prores", "quiesceHWLLTTraffic", 0x2979dfc, 0x3b4),
    ("prores", "activateDevice", 0x297c2a4, 0x4c4),
    ("prores", "isPSDSupported", 0x297ee4c, 0x14),
    ("prores", "SetDevFeatureList", 0x2981918, 0x1a8),
    ("prores", "EncodeFrame", 0x2982f0c, 0xc10),
    ("prores", "EncodeYCbCr_v1", 0x2986e0c, 0x9b0),
    ("prores", "EncodeYCbCr_v0", 0x29877bc, 0x808),
    ("ave", "MakeFwCmd_Config", 0x1e506ec, 0x39c),
    ("ave", "MakeFwCmd_Halt", 0x1e50a88, 0x14c),
    ("ave", "SendFwCmd_Start", 0x1e53650, 0x95c),
    ("ave", "SendFwCmd_Process", 0x1e56b20, 0x9e4),
    ("ave", "MakeFwCmd_Process_AVC", 0x1dad900, 0x7b4),
    ("ave", "MakeFwCmd_Process_HEVC", 0x1dae0b4, 0x81c),
    ("ave", "StartUpIOP", 0x1e69cbc, 0x2a88),
    ("ane", "InitFirmwareConfiguration", 0x2305824, 0x15b4),
    ("ane", "LoadFirmware_gated", 0x2310e90, 0x83c),
    ("ane", "waitForFirmwareToQuiesce", 0x230cc04, 0x284),
)
AVE_LENGTHS_OFFSET = 0x2b682c
AVE_LENGTHS = (0x3b40, 0x13ee0, 0x3920, 0x6e08, 0x78c8, 0x6090)


FIRMWARE_PINS = {
    "ave": ("3accb61ec6ad2267cf43068ab94c04c49f0f003b963ad817b1b7142b1ab3ae92", b"avef", (0x158000, 0x134000)),
    "ane": ("9e22fcb602447764761e6645e996c28fa0befc80e6686533b175fa6ca7679e05", b"anef", (0xec000, 0x280000)),
}


def firmware_facts(raw, engine):
    pin, kind, sizes = FIRMWARE_PINS[engine]
    if prores.sha(raw) != pin:
        raise prores.Refused("not the inspected retained 26C5035r " + engine + " image")
    tag, body, end = prores.tlv(raw, 0)
    if tag != 0x30 or end != len(raw):
        raise prores.Refused("unexpected retained firmware container")
    fields, position = [], 0
    for _ in range(4):
        tag, value, position = prores.tlv(body, position)
        fields.append((tag, value))
    if (position != len(body) or fields[:3] != [(0x16, b"IM4P"), (0x16, kind), (0x16, b"1")] or
            fields[3][0] != 4):
        raise prores.Refused("unexpected retained firmware IM4P fields")
    image = fields[3][1]
    if struct.unpack_from("<I", image)[0] != 0xfeedfacf:
        raise prores.Refused("retained media firmware is not Mach-O64")
    ncmd = struct.unpack_from("<I", image, 16)[0]
    position, segments = 32, []
    for _ in range(ncmd):
        cmd, size = struct.unpack_from("<II", image, position)
        prores.span(image, position, size)
        if cmd == 0x19:
            name = image[position + 8:position + 24].split(b"\0")[0].decode("ascii")
            dva, memory_bytes, offset, file_bytes = struct.unpack_from("<4Q", image, position + 24)
            if name in ("__TEXT", "__DATA"):
                if file_bytes > memory_bytes or memory_bytes > 4 * 1024 * 1024:
                    raise prores.Refused("retained firmware segment dimensions differ")
                data = bytes(prores.span(image, offset, file_bytes)) + bytes(memory_bytes - file_bytes)
                segments.append({"name": name, "relative_dva": dva, "memory_bytes": memory_bytes,
                                 "file_offset": offset, "file_bytes": file_bytes,
                                 "memory_image_sha256": prores.sha(data), "memory_image_crc32": zlib.crc32(data)})
        position += size
    if ([s["name"] for s in segments] != ["__TEXT", "__DATA"] or
            tuple(s["memory_bytes"] for s in segments) != sizes):
        raise prores.Refused("retained exact-build TEXT/DATA geometry differs")
    return {"source_sha256": pin, "segments": segments,
            "source": "retained-file-only-not-live-preload", "compatibility_qualified": False}


def source_facts(decoded):
    if len(decoded) != prores.DECODED_SIZE or prores.sha(decoded) != prores.DECODED_SHA256:
        raise prores.Refused("native inspection requires the exact pinned decoded collection")
    lengths = struct.unpack_from("<6I", decoded, AVE_LENGTHS_OFFSET)
    if lengths != AVE_LENGTHS:
        raise prores.Refused("native AVE command-size ledger differs")
    capacities = [(off, mask, value) for off, mask, value in
                  prores.native_rows(decoded, 0x12068b0, 80) if off in (0x394, 0x714)]
    if capacities != [(0x394, 0xffffffc0, 0x200000), (0x714, 0xffffffc0, 0x200000)]:
        raise prores.Refused("native ProRes statistics rows differ")
    return {
        "ave": {"native_command_size_table_file_offset": AVE_LENGTHS_OFFSET,
                "native_start_bytes": {"avc": lengths[0], "hevc": lengths[1], "av1": lengths[2]},
                "native_process_bytes": {"avc": lengths[3], "hevc": lengths[4], "av1": lengths[5]},
                "pinned_26A428_process_bytes": {"avc": 0x6df8, "hevc": 0x78b8},
                "process_extension_bytes": 16,
                "compatibility": "not-equivalent-to-pinned-command-lengths",
                "remaining_contract": "derive all changed PICMGMT/codec field semantics, replies, boot and Halt from native trace"},
        "prores": {"native_statistics_rows": capacities,
                   "native_activation": "MODE_OFF; exact tunables; ring and statistics; MODE_RUN; IRQs; tables; publish",
                   "native_quiescence": "separate bulk/LLT wrapper request/status polling, selected by DevID and engine version",
                   "native_ycbcr_descriptor_selection": "SetDevFeatureList maps hardware version 4/5 to v1, others to v0",
                   "remaining_contract": "live engine ID/version, same-unit first-job descriptor, reset/DART and power-state witnesses"},
        "ane": {"current_preload_bytes": [0xec000, 0x280000],
                "pinned_preload_bytes": [0xc0000, 0x2ac000],
                "both_total_bytes": 0x36c000, "h17_0x380000_span_tail_bytes": 0x14000,
                "compatibility": "equal-total-span-does-not-prove-DATA-relocations-or-boot-ABI",
                "remaining_contract": "live TEXT pin, exact DATA relocation/patchbay/boot argument ledger and native cold-state/stop trace"},
    }


def inspect(decoded):
    facts = source_facts(decoded)
    if importlib.metadata.version("capstone") != "5.0.6":
        raise prores.Refused("native inspection requires capstone==5.0.6")
    import capstone
    decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    methods = []
    for engine, name, offset, size in SPANS:
        code = prores.span(decoded, offset, size)
        # Disassembly is labelled by file offset; no physical address is inferred.
        decoded_instructions = list(decoder.disasm(bytes(code), offset))
        instructions = [{"file_offset": ins.address, "mnemonic": ins.mnemonic, "operands": ins.op_str}
                        for ins in decoded_instructions]
        if sum(ins.size for ins in decoded_instructions) != size:
            raise prores.Refused("native function span did not fully disassemble")
        methods.append({"engine": engine, "name": name, "file_offset": offset, "bytes": size,
                        "sha256": prores.sha(code), "instructions": instructions})
    return {"schema": 1, "recipe": "j716s-26C5035r-native-media-contract-v1",
            "board": "j716s", "model": "Mac17,8", "macos_build": "26C5035r",
            "kernel_commit": prores.KERNEL_COMMIT, "source_sha256": prores.SOURCE_SHA256,
            "decoded_sha256": prores.DECODED_SHA256, "capstone_version": "5.0.6",
            "facts": facts, "native_methods": methods,
            "first_dma_authorized": False, "firmware_installed": False, "engine_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernelcache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new private JSON under outputs/firmware")
    parser.add_argument("--ave-im4p", type=Path, help="exact retained private AVE image; requires --ane-im4p")
    parser.add_argument("--ane-im4p", type=Path, help="exact retained private ANE image; requires --ave-im4p")
    args = parser.parse_args()
    try:
        prores.profile()
        if not Path(os.path.abspath(args.output)).is_relative_to(PROJECT / "outputs/firmware"):
            raise prores.Refused("native inspection output must remain private under outputs/firmware")
        decoded = prores.decode_source(prores.private_input(args.kernelcache, prores.SOURCE_SIZE))
        report = inspect(decoded)
        if bool(args.ave_im4p) != bool(args.ane_im4p):
            raise prores.Refused("retained firmware inspection requires both exact AVE and ANE images")
        if args.ave_im4p:
            report["retained_firmware"] = {engine: firmware_facts(prores.private_input(path, 4 * 1024 * 1024), engine)
                                           for engine, path in (("ave", args.ave_im4p), ("ane", args.ane_im4p))}
        parent, name = prores.open_parent(args.output)
        try:
            raw = (json.dumps(report, indent=2) + "\n").encode()
            prores.write_new(parent, name, raw)
            os.fsync(parent)
        finally:
            os.close(parent)
        print(json.dumps({"native_report_sha256": prores.sha(raw), "facts": report["facts"],
                          "first_dma_authorized": False, "firmware_installed": False, "engine_started": False}))
    except (ValueError, OSError, KeyError, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(1, "Refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()
