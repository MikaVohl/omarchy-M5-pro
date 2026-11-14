#!/usr/bin/env python3
"""Emit metadata-only proof for the exact public J716s 26C5035r firmware inputs.

Uses macOS libcompression or Linux liblzfse; never executes firmware or contacts a device.
"""
import argparse
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import struct
import sys
import uuid

KERNEL_REF = "8ab10100a4c1c944797e3fc8345589aff57777df"
EXPECTED_GPU = "b5537c8f06bd19ebf0efd0cbfb6552339a10d4afc4eda9d21cbedc3666763600"
OLD_UUID = "64863924-B56B-3E32-87FE-038677F52709"
NEW_UUID = "58CB3FFC-B2BA-352E-B20B-71A91B15FA9F"
FILLED = [(0x224, 4), (0x23c, 8), (0x4976c, 0x740), (0x49eac, 0x231),
          (0x50224, 4), (0x5023c, 8), (0x9d1ec, 0x740), (0x9d92c, 0x231)]


def digest(b):
    return hashlib.sha256(b).hexdigest()


def der(b, p=0):
    tag, n = b[p:p+2]
    start = p + 2
    if n & 128:
        count = n & 127
        n = int.from_bytes(b[start:start+count], "big")
        start += count
    if start + n > len(b):
        raise ValueError("truncated DER")
    return tag, start, n, start + n


def payload(path):
    b = path.read_bytes()
    tag, p, _, end = der(b)
    assert tag == 0x30
    children = []
    while p < end:
        tag, start, length, p = der(b, p)
        children.append((tag, b[start:start+length]))
    assert children[0] == (0x16, b"IM4P")
    return next(value for tag, value in children if tag == 4)


def decompress(b):
    if b[:4] != b"bvx2":
        raise ValueError("not an LZFSE firmware payload")
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libcompression.dylib")
        f = lib.compression_decode_buffer
        f.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
        extra = (0x801,)
    else:
        library = ctypes.util.find_library("lzfse") or "liblzfse.so.1"
        lib = ctypes.CDLL(library)
        f = lib.lzfse_decode_buffer
        f.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_size_t, ctypes.c_void_p]
        extra = ()
    f.restype = ctypes.c_size_t
    out = ctypes.create_string_buffer(64 * 1024 * 1024)
    length = f(out, len(out), b, len(b), None, *extra)
    if not length or length >= len(out):
        raise ValueError("LZFSE decode failed or exceeded the 64 MiB firmware bound")
    return out.raw[:length]


def commands(b, start=0):
    assert b[start:start+4] == bytes.fromhex("cffaedfe")
    ncmds = struct.unpack_from("<I", b, start + 16)[0]
    if ncmds > 300:
        raise ValueError("bad Mach-O header")
    p = start + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", b, p)
        if not 8 <= size <= 65536 or p + size > len(b):
            raise ValueError("bad Mach-O command")
        yield cmd, p
        p += size


def dcp_info(path):
    b = decompress(payload(path))
    images = []
    p = 0
    while True:
        p = b.find(bytes.fromhex("cffaedfe"), p)
        if p < 0:
            break
        try:
            for cmd, q in commands(b, p):
                if cmd == 0x1b:
                    images.append({"offset": p,
                                   "uuid": str(uuid.UUID(bytes=b[q+8:q+24])).upper()})
        except (ValueError, struct.error):
            pass
        p += 4
    begin = b.index(b"AppleDCP-")
    version = b[begin:b.index(b"\0", begin)].decode()
    return {"im4p_sha256": digest(path.read_bytes()), "decoded_sha256": digest(b),
            "decoded_size": len(b), "version": version, "macho_uuids": images}


def gpu_texts(path):
    b = payload(path)
    assert b[32:40] == b"rkosftab"
    result = {}
    for i in range(struct.unpack_from("<I", b, 40)[0]):
        tag, off, size, _ = struct.unpack_from("<4sIII", b, 48 + 16*i)
        image = b[off:off+size]
        assert len(image) == size
        for cmd, p in commands(image):
            if cmd == 0x19 and image[p+8:p+24].rstrip(b"\0") == b"__TEXT":
                _, _, fileoff, filesz = struct.unpack_from("<QQQQ", image, p+24)
                result[tag.decode()] = image[fileoff:fileoff+filesz]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--baseline-dcp", type=Path, help="optional public 26A428 baseline")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fw = args.firmware_dir
    primary = gpu_texts(fw / "agx/armfw_g17x.im4p")["a010"]
    secondary = gpu_texts(fw / "agx/armfw1_g17x.im4p")["a010"]
    assert len(primary) == 0x50000 and len(secondary) == 0x54000
    text = bytearray(primary + secondary)
    for off, tag in [(0x49eac, b"GKTS"), (0x49f8f, b"ECAP"),
                     (0x9d92c, b"GKTS"), (0x9da0f, b"ECAP")]:
        assert text[off:off+8] == tag + struct.pack("<I", 8)
    for off, length in FILLED:
        text[off:off+length] = bytes(length)
    assert digest(text) == EXPECTED_GPU
    new = dcp_info(fw / "dcp/t605xdcp.im4p")
    # Select the pinned third embedded UUID, not RTKit's first UUID or the DNUB bundle UUID.
    if len(new["macho_uuids"]) != 4 or new["macho_uuids"][2]["uuid"] != NEW_UUID:
        raise ValueError("DCP does not match the exact J716s 26C5035r profile")
    result = {"kernel_ref": KERNEL_REF, "gpu": {"ftab_tag": "a010",
              "text_size": len(text), "normalized_sha256": digest(text),
              "boot_filled_ranges": FILLED}, "dcp_26C5035r": new,
              "scope": "Static identity and public-source integration proof; no hardware test."}
    if args.baseline_dcp:
        old = dcp_info(args.baseline_dcp)
        if len(old["macho_uuids"]) != 4 or old["macho_uuids"][2]["uuid"] != OLD_UUID:
            raise ValueError("baseline DCP does not match the public 26A428 profile")
        result["dcp_26A428"] = old
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
