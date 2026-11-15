#!/usr/bin/env python3
"""Prepare a private, offline J716s / 26C5035r APRI candidate from its own macOS.

Never installs firmware, loads modules, accesses devices, or changes boot state.
The exact retained krnl IM4P is pinned before decoding; no table searching,
foreign-SoC substitution, dynamic callbacks, or guessed register writes.
Requires lzfse==0.4.2 only for prepare (not for offline parser tests/check).
A structurally valid candidate is NOT physical ProRes qualification.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import struct

PROJECT = Path(__file__).resolve().parents[1]
KERNEL_COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
SOURCE_SHA256 = "66bbe2fa3edfae6fe7b479ce78a7e3935ea29ae2f155e0f4ef45cd5b03594720"
DECODED_SHA256 = "d1003b5143ffd88439b4d45b6017f587171cfe6855660bed05370a2ea5db9fe9"
SOURCE_SIZE, DECODED_SIZE = 32709948, 124518400
FIRMWARE_SHA256 = "d1b4c81d293e3c40fa0e0ce7dad4d576ccb05a0b6a1c6a15fb21991341bdaa22"
FIRMWARE = "prores-t6050-init.bin"
REPORT = "prores-preparation.json"
RECIPE = "j716s-26C5035r-prores-627-native-v1"
COUNTS, LIMITS = (82, 80, 62, 12), (0x4000, 0x4000, 0x1000000, 0x4000)
FIRST_DMA_BLOCKERS = (
    "reviewed depth-one DMA quarantine patch and full kernel/DT/module verification",
    "live T6050 engine ID/version and native v1 descriptor/IRQ/statistics evidence",
    "SCODEC/APR/DART quiescence, preserved translations and native power/boot contract",
    "exact-board attended one-shot trial with physical power-cycle fallback",
)
# Mach-O native pointer lists selected by AppleProResHW 627's T6050/core0
# paths in SetScodecPSTunablesByDevID, SetProResTunablesByDevID and
# SetAXI2AFTunablesByDevID. These are FILE offsets, not MMIO addresses.
TABLES = (
    ("system", 0x120f5c8, 82, 0),
    ("core", 0x12068b0, 80, 0),
    ("wrapper", 0x120d7e0, 62, 0),
    ("control-ced", 0x120b778, 11, 0),
    # macOS maps SCODEC sys/CED/SSW from memory indices 2/3/4.
    # On J716s SSW is 0x1000 above CED, inside Linux's 0x4000 ctrl aperture.
    ("control-ssw", 0x120b988, 1, 0x1000),
)
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class Refused(ValueError):
    """Non-secret refusal suitable for stderr."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def open_parent(path):
    path = Path(path)
    if ".." in path.parts:
        raise Refused("path traversal is forbidden")
    path = Path(os.path.abspath(path))
    fd = os.open("/", FLAGS | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def private_input(path, maximum):
    parent, name = open_parent(path)
    try:
        fd = os.open(name, FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise Refused("input must be a private single-link regular file")
        if info.st_size > maximum:
            raise Refused("input exceeds its size limit")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise Refused("input exceeds its size limit")
        return data
    finally:
        os.close(fd)


def tlv(data, offset):
    if offset + 2 > len(data):
        raise Refused("truncated IM4P field")
    tag, size = data[offset:offset + 2]
    offset += 2
    if size & 0x80:
        n = size & 0x7f
        if not 1 <= n <= 4 or offset + n > len(data) or data[offset] == 0:
            raise Refused("invalid IM4P field length")
        size = int.from_bytes(data[offset:offset + n], "big")
        offset += n
        if size < 128:
            raise Refused("noncanonical IM4P field length")
    end = offset + size
    if end > len(data):
        raise Refused("truncated IM4P field")
    return tag, data[offset:end], end


def decode_source(source):
    # Refuse before entering a parser/decompressor; only the exact inspected
    # retained 26C5035r image is admitted, not a user-provided table map.
    if len(source) != SOURCE_SIZE or sha(source) != SOURCE_SHA256:
        raise Refused("not the inspected J716s 26C5035r kernelcache IM4P")
    tag, body, end = tlv(source, 0)
    if tag != 0x30:
        raise Refused("kernelcache is not IM4P")
    fields, pos = [], 0
    for _ in range(4):
        tag, value, pos = tlv(body, pos)
        fields.append((tag, value))
    if (fields[0] != (0x16, b"IM4P") or fields[1] != (0x16, b"krnl") or
            fields[2][0] != 0x16 or fields[3][0] != 4 or
            not fields[3][1].startswith(b"bvx2")):
        raise Refused("unexpected kernelcache compression/type")
    if importlib.metadata.version("lzfse") != "0.4.2":
        raise Refused("prepare requires lzfse==0.4.2")
    import lzfse
    decoded = lzfse.decompress(fields[3][1])
    if len(decoded) != DECODED_SIZE or sha(decoded) != DECODED_SHA256:
        raise Refused("decoded kernelcollection differs from the inspected source")
    return decoded


def span(data, offset, size):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise Refused("native table points outside the kernelcollection")
    return memoryview(data)[offset:offset + size]


def native_pointer(value, data, size):
    # Only the two unbound, unauthenticated pointer encodings present in the
    # pinned KC lists/records. No generic relocation or function emulation.
    if value >> 30 not in (0x400000, 0x800000):
        raise Refused("unsupported native table pointer encoding")
    offset = value & 0x3fffffff
    span(data, offset, size)
    return offset


def native_rows(data, table, count, adjustment=0):
    pointers = struct.unpack("<" + "Q" * (count + 1), span(data, table, (count + 1) * 8))
    if pointers[-1] or any(not p for p in pointers[:-1]):
        raise Refused("native table count/terminator differs")
    rows = []
    for pointer in pointers[:-1]:
        off = native_pointer(pointer, data, 16)
        word, reserved, parameters = struct.unpack("<IIQ", span(data, off, 16))
        if word >> 28 != 4 or reserved:
            raise Refused("native row is not a static 32-bit register operation")
        payload = native_pointer(parameters, data, 16)
        mask, value, callback = struct.unpack("<IIQ", span(data, payload, 16))
        if callback:
            raise Refused("dynamic native tunable callbacks are not extractable")
        rows.append(((word & 0x0fffffff) + adjustment, mask, value))
    return rows


def encode_apri(blocks):
    if len(blocks) != 4:
        raise Refused("T6050 requires four APRI blocks")
    parts = [struct.pack("<4sIII", b"APRI", 1, 0x6050, 4)]
    for index, rows in enumerate(blocks):
        if len(rows) != COUNTS[index]:
            raise Refused("T6050 APRI block has the wrong row count")
        seen = set()
        parts.append(struct.pack("<II", index + 1, len(rows)))
        for offset, mask, value in rows:
            if (any(type(v) is not int or not 0 <= v <= 0xffffffff for v in (offset, mask, value)) or
                    offset % 4 or offset >= LIMITS[index] or offset in seen or
                    not mask or value & ~mask):
                raise Refused("APRI row has invalid offset/mask/value")
            seen.add(offset)
            parts.append(struct.pack("<III", offset, mask, value))
    return b"".join(parts)


def validate_apri(data):
    if len(data) != 2880 or data[:16] != struct.pack("<4sIII", b"APRI", 1, 0x6050, 4):
        raise Refused("invalid T6050 APRI header/size")
    pos, blocks = 16, []
    for i, count in enumerate(COUNTS):
        if struct.unpack_from("<II", data, pos) != (i + 1, count):
            raise Refused("invalid APRI block order/count")
        pos += 8
        blocks.append([struct.unpack_from("<III", data, pos + 12 * j) for j in range(count)])
        pos += 12 * count
    if encode_apri(blocks) != data:
        raise Refused("APRI did not round-trip")
    return {"bytes": len(data), "sha256": sha(data), "rows": list(COUNTS)}


def prepare_image(decoded):
    if len(decoded) != DECODED_SIZE or sha(decoded) != DECODED_SHA256:
        raise Refused("not the pinned decoded kernelcollection")
    rows = [native_rows(decoded, offset, count, adjust) for name, offset, count, adjust in TABLES]
    data = encode_apri(rows[:3] + [rows[3] + rows[4]])
    if sha(data) != FIRMWARE_SHA256:
        raise Refused("extracted APRI differs from the inspected recipe")
    return data


def profile():
    p = json.loads((PROJECT / "profiles/j716s.json").read_text())
    if ((p["model"], p["board"], p["soc"], p["macos_build"]) !=
            ("Mac17,8", "j716s", "t6050", "26C5035r") or
            p["sources"]["kernel"]["commit"] != KERNEL_COMMIT):
        raise Refused("profile/kernel pin changed; requalify the ProRes recipe")


def write_new(parent, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def save_candidate(path, data):
    # Publishing under the existing private firmware-output convention only;
    # there is intentionally no install path or engine rebind command.
    destination = Path(os.path.abspath(path))
    if not destination.is_relative_to(PROJECT / "outputs/firmware"):
        raise Refused("candidate must remain under outputs/firmware")
    validate_apri(data)
    if sha(data) != FIRMWARE_SHA256:
        # Parser fixtures and user-authored rows are never publishable candidates.
        raise Refused("not the source-pinned ProRes firmware candidate")
    parent, name = open_parent(path)
    directory = apple = None
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        directory = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
        os.mkdir("apple", 0o700, dir_fd=directory)
        apple = os.open("apple", FLAGS | os.O_DIRECTORY, dir_fd=directory)
        write_new(apple, FIRMWARE, data)
        report = {"schema": 1, "recipe": RECIPE, "model": "Mac17,8", "board": "j716s",
                  "soc": "t6050", "macos_build": "26C5035r", "kext_version": "627",
                  "kernel_commit": KERNEL_COMMIT, "source_sha256": SOURCE_SHA256,
                  "decoded_sha256": DECODED_SHA256, "apri": validate_apri(data),
                  "control_translation": "11 CED rows plus 1 SSW row at ctrl+0x1000",
                  "apple_signature_verification": "not-performed",
                  "hardware_qualification": "not-performed", "installation": "not-performed"}
        write_new(directory, REPORT, (json.dumps(report, indent=2) + "\n").encode())
        os.fsync(apple)
        os.fsync(directory)
        os.fsync(parent)
    finally:
        if apple is not None:
            os.close(apple)
        if directory is not None:
            os.close(directory)
        os.close(parent)


def checked_candidate(path):
    data = private_input(Path(path) / "apple" / FIRMWARE, 2880)
    report = json.loads(private_input(Path(path) / REPORT, 16384))
    expected = {"schema": 1, "recipe": RECIPE, "model": "Mac17,8", "board": "j716s",
                "soc": "t6050", "macos_build": "26C5035r", "kext_version": "627",
                "kernel_commit": KERNEL_COMMIT, "source_sha256": SOURCE_SHA256,
                "decoded_sha256": DECODED_SHA256, "apri": validate_apri(data),
                "hardware_qualification": "not-performed", "installation": "not-performed"}
    if (not isinstance(report, dict) or sha(data) != FIRMWARE_SHA256 or
            any(report.get(k) != v for k, v in expected.items())):
        raise Refused("candidate identity/integrity differs from the pinned recipe")
    return expected["apri"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--kernelcache", type=Path, help="private retained krnl IM4P, not a foreign kext")
    source.add_argument("--check", type=Path, help="check a prepared private candidate offline")
    parser.add_argument("--output", type=Path, help="new private directory under outputs/firmware")
    args = parser.parse_args()
    try:
        profile()
        if args.check:
            if args.output:
                raise Refused("--output is not accepted with --check")
            result = checked_candidate(args.check)
        else:
            if not args.output:
                raise Refused("--kernelcache requires --output")
            data = prepare_image(decode_source(private_input(args.kernelcache, SOURCE_SIZE)))
            save_candidate(args.output, data)
            result = validate_apri(data)
        print(json.dumps({"candidate": result, "installed": False, "engine_started": False,
                          "first_dma_authorized": False, "first_dma_blockers": FIRST_DMA_BLOCKERS}))
    except (ValueError, OSError, KeyError, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(1, "Refused: " + (str(error) if isinstance(error, Refused) else
                                    "input, decoder or profile unavailable/invalid") + "\n")


if __name__ == "__main__":
    main()
