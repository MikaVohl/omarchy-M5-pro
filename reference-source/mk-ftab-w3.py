#!/usr/bin/env python3
# Build the pinned J716s Centauri FTAB by appending its nonce-free global IM4M.
# Inputs and output are explicit CLI paths; firmware is never distributed here.
# The checks establish pinned bytes, manifest properties and SHA-384 image
# matches, not an independent verification of Apple's certificate chain.
#
# FTAB layout (little endian; the 'ftab' format of Apple's RTKit accessories, as ftabutil and
# blacktop/ipsw read it):
#   0x00 u32 unk0, 0x04 u32 unk1, 0x08 u32 unk2, 0x0c u32 unk3,
#   0x10 u32 ticket_offset, 0x14 u32 ticket_len, 0x18 u32 unk4, 0x1c u32 unk5,
#   0x20 "rkosftab", 0x28 u32 entry count, 0x2c u32 unk6,
#   0x30 entries: {char tag[4]; u32 offset; u32 size; u32 unk}, then the entry data,
#   then (optional) the ticket, unpadded, right after the last entry.
# The restore bundle's file has ticket_offset = ticket_len = 0 and ends with the last entry.
# This script appends the IM4M at the old end of file and sets 0x10/0x14; nothing else changes.
#
import argparse
import hashlib
import json
import os
import pathlib
import struct
import sys

PROJECT = pathlib.Path(__file__).resolve().parents[1]

# What the J716s manifest must claim (production-fused N1, CHIP 0x2026, board 0x841).
WANT_CHIP, WANT_BORD = 0x2026, 0x841
MAX_DRIVER = 64 << 20  # centauri_rom_boot(): 0x30 <= size <= SZ_64M, "rkosftab" at 0x20


def die(msg):
    print("REFUSED: " + msg)
    sys.exit(1)


# ---------------------------------------------------------------- FTAB
def ftab_parse(d, what):
    if len(d) < 0x30 or d[0x20:0x28] != b"rkosftab":
        die(f"{what}: no rkosftab magic at 0x20")
    hdr = struct.unpack_from("<8I", d, 0)
    n, unk6 = struct.unpack_from("<2I", d, 0x28)
    table_end = 0x30 + 16 * n
    if not 0 < n < 1024 or table_end > len(d):
        die(f"{what}: entry count {n} does not fit")
    ents, ranges = [], []
    for i in range(n):
        tag, off, size, unk = struct.unpack_from("<4sIII", d, 0x30 + 16 * i)
        if off < table_end or off + size > len(d):
            die(f"{what}: entry {i} {tag!r} 0x{off:x}+0x{size:x} out of bounds")
        ents.append((tag, off, size, unk))
        ranges.append((off, off + size))
    ranges.sort()
    for (a0, a1), (b0, b1) in zip(ranges, ranges[1:]):
        if b0 < a1:
            die(f"{what}: overlapping entries at 0x{b0:x}")
    tags = [e[0] for e in ents]
    if len(set(tags)) != len(tags):
        die(f"{what}: duplicate tags")
    return dict(hdr=hdr, n=n, unk6=unk6, ents=ents, table_end=table_end,
                data_end=max(r[1] for r in ranges), tkt_off=hdr[4], tkt_len=hdr[5])


# ---------------------------------------------------------------- DER (just enough for IM4M)
def der(b, i):
    """-> (cls_byte, tagnum, value_start, value_len). Handles high-tag-number (private 4CC) tags."""
    t = b[i]
    i += 1
    num = t & 0x1F
    if num == 0x1F:
        num = 0
        while True:
            c = b[i]
            i += 1
            num = (num << 7) | (c & 0x7F)
            if not c & 0x80:
                break
    L = b[i]
    i += 1
    if L & 0x80:
        k = L & 0x7F
        if not 0 < k <= 4:
            raise ValueError("bad DER length")
        L = int.from_bytes(b[i:i + k], "big")
        i += k
    if i + L > len(b):
        raise ValueError("DER element runs past its container")
    return t, num, i, L


def children(b, start, length):
    i, end = start, start + length
    while i < end:
        t, num, v, L = der(b, i)
        yield t, num, v, L
        i = v + L
    if i != end:
        raise ValueError("DER children overrun")


def fourcc(num):
    return num.to_bytes(4, "big") if num.bit_length() <= 32 else b"????"


def im4m_parse(b):
    """-> dict: manp {key: (type, value)} and images {tag: {key: (type, value)}}.
    Values are kept in memory for the checks only; nothing here is printed except key names."""
    t, _, v, L = der(b, 0)
    if t != 0x30 or v + L != len(b):
        die("IM4M: not one DER SEQUENCE spanning the file")
    top = list(children(b, v, L))
    if len(top) < 4 or top[0][0] != 0x16 or b[top[0][2]:top[0][2] + top[0][3]] != b"IM4M":
        die("IM4M: no 'IM4M' header")
    if top[2][0] != 0x31:
        die("IM4M: no manifest body SET")
    if top[3][0] != 0x04:
        die("IM4M: no signature OCTET STRING")

    def props(start, length):
        out = {}
        for t, num, v, L in children(b, start, length):
            if t & 0xE0 != 0xE0:  # private, constructed [4CC]
                raise ValueError("unexpected element in a property set")
            (st, _, sv, sl), = [c for c in children(b, v, L)]
            parts = list(children(b, sv, sl))
            name = b[parts[0][2]:parts[0][2] + parts[0][3]]
            if name != fourcc(num):
                raise ValueError("property name mismatch")
            pt, _, pv, pl = parts[1]
            out[name] = (pt, b[pv:pv + pl], pv, pl)
        return out

    manb = None
    for t, num, v, L in children(b, top[2][2], top[2][3]):
        if fourcc(num) == b"MANB":
            manb = (v, L)
    if manb is None:
        die("IM4M: no MANB")
    (st, _, sv, sl), = list(children(b, *manb))
    seq = list(children(b, sv, sl))
    if b[seq[0][2]:seq[0][2] + seq[0][3]] != b"MANB" or seq[1][0] != 0x31:
        die("IM4M: malformed MANB")
    manp, images = None, {}
    for t, num, v, L in children(b, seq[1][2], seq[1][3]):
        (st2, _, sv2, sl2), = list(children(b, v, L))
        inner = list(children(b, sv2, sl2))
        name = b[inner[0][2]:inner[0][2] + inner[0][3]]
        if name != fourcc(num) or inner[1][0] != 0x31:
            die("IM4M: malformed MANB entry")
        p = props(inner[1][2], inner[1][3])
        if name == b"MANP":
            manp = p
        else:
            images[name] = p
    if manp is None:
        die("IM4M: no MANP")
    return manp, images


def as_int(p, k):
    t, val, _, _ = p[k]
    if t != 0x02:
        die(f"IM4M: MANP {k.decode()} is not an INTEGER")
    return int.from_bytes(val, "big")


def as_bool(p, k):
    t, val, _, _ = p[k]
    if t != 0x01 or len(val) != 1:
        die(f"IM4M: MANP {k.decode()} is not a BOOLEAN")
    return val[0] != 0


# ---------------------------------------------------------------- checks
def check_manifest(m, ftab_bytes, f):
    manp, images = im4m_parse(m)
    keys = sorted(k.decode("latin1") for k in manp)
    print(f"im4m: {len(m)} bytes, MANP keys {' '.join(keys)}")
    # Per-unit personalisation would show up as ECID and a nonce (BNCH/snon). A global manifest has neither.
    for k in (b"ECID", b"BNCH", b"snon", b"Nonc"):
        if k in manp:
            die(f"IM4M: MANP has {k.decode()}: this is a per-unit ticket, not the static manifest")
    if as_int(manp, b"CHIP") != WANT_CHIP or as_int(manp, b"BORD") != WANT_BORD:
        die("IM4M: CHIP/BORD are not the J716s N1's (0x2026/0x841)")
    if not (as_bool(manp, b"CPRO") and as_bool(manp, b"CSEC")):
        die("IM4M: not the production manifest (CPRO/CSEC must be true): wrong .im4m variant")
    print(f"im4m: global (no ECID, no nonce), CHIP 0x{WANT_CHIP:x} BORD 0x{WANT_BORD:x}, CPRO 1 CSEC 1 (production)")
    ents = {e[0]: e for e in f["ents"]}
    ok = 0
    for tag, p in sorted(images.items()):
        if b"DGST" not in p:
            die(f"IM4M: image {tag.decode()} has no DGST")
        if tag not in ents:
            die(f"IM4M: image {tag.decode()} has no FTAB entry")
        _, off, size, _ = ents[tag]
        dg = p[b"DGST"][1]
        if len(dg) != 48 or hashlib.sha384(ftab_bytes[off:off + size]).digest() != dg:
            die(f"IM4M: digest of image {tag.decode()} does not match its FTAB entry")
        ok += 1
    unsigned = sorted(t.decode("latin1") for t in ents if t not in images)
    print(f"im4m: {ok}/{len(images)} image digests (SHA-384) match their FTAB entries: "
          f"{' '.join(sorted(t.decode() for t in images))}")
    print(f"ftab: {len(unsigned)} entries not covered by the manifest (data/config, not loaded as code)")
    return images


def build(firmware, manifest, output, report):
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    if (profile["model"], profile["board"], profile["macos_build"]) != ("Mac17,8", "j716s", "26C5035r"):
        die("profile is not the qualified J716s 26C5035r identity")
    out = output.resolve()
    if output.exists() or report.exists() or output.is_symlink() or report.is_symlink():
        die("refusing existing firmware or report")
    for path in (firmware, manifest):
        if not path.is_file() or path.is_symlink():
            die("missing or symlinked input " + str(path))
    d, m = firmware.read_bytes(), manifest.read_bytes()
    pins = profile["n1"]
    if hashlib.sha256(d).hexdigest() != pins["input_sha256"]:
        die("input FTAB differs from profile pin")
    if hashlib.sha256(m).hexdigest() != pins["manifest_sha256"]:
        die("global IM4M differs from profile pin")
    f = ftab_parse(d, "input ftab")
    print(f"ftab: {len(d)} bytes, {f['n']} entries, header word0 0x{f['hdr'][0]:x} word1 0x{f['hdr'][1]:x}")
    print("ftab: tags " + " ".join(e[0].decode("latin1") for e in f["ents"]))
    if f["tkt_off"] or f["tkt_len"]:
        die("input ftab already carries a ticket (0x10/0x14 not zero)")
    if any(f["hdr"][i] for i in (2, 3, 4, 5, 6, 7)) or f["unk6"]:
        die("input ftab header words 0x08-0x1f / 0x2c are not all zero: unknown variant")
    if f["data_end"] != len(d):
        die(f"input ftab has 0x{len(d) - f['data_end']:x} bytes after its last entry")
    if len(d) % 4:
        die("input ftab length is not 4-aligned")
    images = check_manifest(m, d, f)

    tkt_off = len(d)
    hdr = bytearray(d[:0x30])
    struct.pack_into("<II", hdr, 0x10, tkt_off, len(m))
    new = bytes(hdr) + d[0x30:] + m

    # Re-verify the result from scratch, as a reader (ftabutil rules + the kernel driver's checks).
    g = ftab_parse(new, "output ftab")
    if (g["n"], g["ents"]) != (f["n"], f["ents"]):
        die("output entry table differs from the input's")
    if new[0x30:tkt_off] != d[0x30:] or new[:0x10] != d[:0x10] or new[0x18:0x30] != d[0x18:0x30]:
        die("output changed bytes other than the ticket fields")
    if (g["tkt_off"], g["tkt_len"]) != (tkt_off, len(m)) or tkt_off + len(m) != len(new):
        die("ticket fields wrong")
    if tkt_off < g["table_end"] or tkt_off < g["data_end"]:
        die("ticket overlaps the table or an entry")
    if new[tkt_off:] != m:
        die("ticket bytes differ from the manifest")
    if not 0x30 <= len(new) <= MAX_DRIVER or new[0x20:0x28] != b"rkosftab":
        die("output fails the driver's size/magic check")
    t, _, v, L = der(new, tkt_off)
    if t != 0x30 or v + L != len(new):
        die("ticket is not one DER element ending at EOF")

    output_sha256 = hashlib.sha256(new).hexdigest()
    if output_sha256 != pins["output_sha256"]:
        die("constructed FTAB differs from qualified output pin")
    result = {"schema": 1, "target_model": profile["model"], "board": profile["board"],
              "macos_build": profile["macos_build"], "input_sha256": pins["input_sha256"],
              "manifest_path": pins["manifest_path"], "manifest_sha256": pins["manifest_sha256"],
              "output_sha256": output_sha256, "output_bytes": len(new),
              "nonce_free_global_manifest": True, "chip": WANT_CHIP, "bord": WANT_BORD,
              "production_cpro_csec": True, "sha384_image_digests_matched": len(images),
              "certificate_chain_independently_verified": False,
              "construction": "append unchanged global IM4M; only FTAB ticket offset/length fields change"}
    out.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(new)
    if out.read_bytes() != new:
        die("readback differs")
    with report.open("x") as fh:
        fh.write(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="Build pinned nonce-free J716s Centauri firmware")
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.manifest, args.output, args.report), indent=2))


if __name__ == "__main__":
    main()
