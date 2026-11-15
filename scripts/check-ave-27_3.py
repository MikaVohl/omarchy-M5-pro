#!/usr/bin/env python3
"""Check private retained J716s/26C5035r AVE inputs offline, without installing.

Requires lzfse==0.4.2. Reads only owner-private, single-link, non-symlink files.
No firmware is copied, installed or loaded and no hardware/boot state is touched.
The upstream-tested 26A428 compatibility protocol is selected deliberately;
native extended PICMGMT/HEVC codec semantics remain unresolved.
"""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import struct
import zlib

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ave_private_input", PROJECT / "scripts/prepare-prores-firmware.py")
private = importlib.util.module_from_spec(spec)
spec.loader.exec_module(private)
FIRMWARE_SHA256 = "3accb61ec6ad2267cf43068ab94c04c49f0f003b963ad817b1b7142b1ab3ae92"
TEXT_SHA256 = "c2e26d92d80fcf37c09cabb9dbbfa42fa9ef007642c7601889b08f1e3dd58445"
DATA_SHA256 = "06da95366db3d3d84fa5d7ade37d5b4bc1d2b1b3ea0a9e4803d0b06bd580e6ca"
NATIVE_LENGTHS = (0x3b40, 0x13ee0, 0x3920, 0x6e08, 0x78c8, 0x6090)
NATIVE_SPANS = (
    ("AVC", 0x1dad900, 0x7b4, "7d4290487e6e91406be0423b2586e7899fe3dfda53948e2de94ae8d9f28ef757"),
    ("HEVC", 0x1dae0b4, 0x81c, "4b830b066c5e555877431cfad7fffb19180898f45a4008a90f711a03aaef9876"),
)


def firmware_contract(raw):
    if private.sha(raw) != FIRMWARE_SHA256:
        raise private.Refused("not the retained exact J716s/26C5035r ave IM4P")
    tag, body, end = private.tlv(raw, 0)
    if tag != 0x30 or end != len(raw):
        raise private.Refused("unexpected AVE container")
    fields, position = [], 0
    for _ in range(4):
        tag, value, position = private.tlv(body, position)
        fields.append((tag, value))
    if (position != len(body) or fields[:3] != [(0x16, b"IM4P"), (0x16, b"avef"), (0x16, b"1")] or
            fields[3][0] != 4):
        raise private.Refused("unexpected AVE IM4P contract")
    image = fields[3][1]
    header = struct.unpack("<8I", private.span(image, 0, 32))
    if header[0] != 0xfeedfacf:
        raise private.Refused("AVE image is not Mach-O64")
    count, command_bytes = header[4:6]
    private.span(image, 32, command_bytes)
    position, segments = 32, []
    for _ in range(count):
        cmd, size = struct.unpack("<II", private.span(image, position, 8))
        if size < 8 or size % 8 or size > 32 + command_bytes - position:
            raise private.Refused("invalid AVE load command")
        if cmd == 0x19:
            if size < 72:
                raise private.Refused("truncated AVE segment")
            name = bytes(private.span(image, position + 8, 16)).split(b"\0")[0]
            dva, memory_bytes, offset, file_bytes = struct.unpack("<4Q", private.span(image, position + 24, 32))
            sections = struct.unpack("<I", private.span(image, position + 64, 4))[0]
            if size != 72 + sections * 80:
                raise private.Refused("invalid AVE segment section span")
            if name in (b"__TEXT", b"__DATA"):
                segments.append((name, dva, memory_bytes, offset, file_bytes))
        position += size
    if position != 32 + command_bytes or segments != [
            (b"__TEXT", 0, 0x158000, 0x4000, 0x158000),
            (b"__DATA", 0x158000, 0x134000, 0x15c000, 0x134000)]:
        raise private.Refused("exact AVE TEXT/DATA geometry differs")
    text = private.span(image, 0x4000, 0x158000)
    data = private.span(image, 0x15c000, 0x134000)
    if (private.sha(text) != TEXT_SHA256 or zlib.crc32(text) != 0xc25b4ddb or
            private.sha(data) != DATA_SHA256 or zlib.crc32(data) != 0x2a05e41e):
        raise private.Refused("exact AVE segment identity differs")
    return {"im4p_sha256": FIRMWARE_SHA256, "text_bytes": 0x158000, "data_bytes": 0x134000,
            "text_image_crc32": "c25b4ddb", "retained_data_image_crc32": "2a05e41e",
            "loader_data_crc_required": True, "retained_data_is_not_loader_snapshot": True}


def native_contract(decoded):
    if len(decoded) != private.DECODED_SIZE or private.sha(decoded) != private.DECODED_SHA256:
        raise private.Refused("not the pinned native decoded kernelcollection")
    if struct.unpack("<6I", private.span(decoded, 0x2b682c, 24)) != NATIVE_LENGTHS:
        raise private.Refused("native command ledger differs")
    for name, offset, size, digest in NATIVE_SPANS:
        if private.sha(private.span(decoded, offset, size)) != digest:
            raise private.Refused("native " + name + " Process constructor differs")
    return {"native_start_bytes": {"avc": 0x3b40, "hevc": 0x13ee0},
            "native_process_bytes": {"avc": 0x6e08, "hevc": 0x78c8},
            "native_picmgmt_bytes": 0x6048,
            "native_picmgmt_offsets": {"avc": 0xdc0, "hevc": 0x1568},
            "native_hevc_codec_copies": [[0x75b0, 0x16c], [0x771c, 0x1a8]],
            "compatibility_process_bytes": {"avc": 0x6df8, "hevc": 0x78b8},
            "selected_protocol": "upstream-tested-26A428-compatibility",
            "native_extension_semantics_resolved": False,
            "native_lengths_admitted": False,
            "proof_limit": "J714s upstream encode/decode; not a local J716s runtime pass",
            "source": "https://github.com/aurora-silicon/linux/pull/1747"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--kernelcache", type=Path, required=True)
    args = parser.parse_args()
    try:
        firmware = firmware_contract(private.private_input(args.firmware, 4 * 1024 * 1024))
        decoded = private.decode_source(private.private_input(args.kernelcache, private.SOURCE_SIZE))
        report = {"schema": 1, "board": "j716s", "model": "Mac17,8", "macos_build": "26C5035r",
                  "kernel_commit": private.KERNEL_COMMIT, "source_sha256": private.SOURCE_SHA256,
                  "decoded_sha256": private.DECODED_SHA256, "firmware": firmware,
                  "commands": native_contract(decoded), "live_handoff_verified": False,
                  "firmware_installed": False, "engine_started": False}
        print(json.dumps(report, indent=2, sort_keys=True))
    except (OSError, ValueError, struct.error, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(1, "Refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()
