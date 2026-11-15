#!/usr/bin/env python3
"""Preserve PRIVATE exact-build camera tuning and current-boot ISP handoff evidence.

prepare reads the current ADT read-only MTD and appleh16camerad from a read-only
mount of this Mac's internal macOS System partition. Outputs are private,
exclusive and hash-anchored. check needs no macOS mount; it checks the anchored
package against this physical unit, firmware pairing and boot. It does not
read ISP MMIO, firmware RAM or camera devices, enable a node, install firmware,
reset or start the ISP, capture frames, alter a boot selection, or reboot.
The 2224 setfile is only a candidate: ADT sensor-type 118 is a platform ID,
NOT proof of IMX958 sensor identity or the 26C5035r camera firmware ABI.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
DT = Path("/sys/firmware/devicetree/base")
REPORT = "camera-firmware-provenance.json"
FIRMWARE = "apple/isp_2224_01XX.dat"
BUILD = "26C5035r"
PARSER_SHA256 = "cfef5935f35c412e006ae70229406b0eac51e830a175ca9b98fcdabe873389f5"
DAEMON_SHA256 = "5a89d5e1dbba90dbe00ca988b98475b4a564bae945c9b99137435845ee9a3689"
SETFILE_MAGIC = 0x22240106
SETFILE_SIZE = 0x6ff4
PADDED_SIZE = (SETFILE_SIZE + 63) & ~63
MAX_INPUT = 32 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(report):
    return (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()


def read_regular(path, private=False):
    if ".." in Path(path).parts:
        raise ValueError("Input path must not contain traversal")
    path = Path(os.path.abspath(path))
    if path.resolve() != path:
        raise ValueError("Input path must not contain symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_INPUT:
            raise ValueError("Input must be a bounded single-link regular file")
        if private and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise ValueError("Package input is not private and owned by this process's user")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(MAX_INPUT + 1)
        if len(data) != info.st_size:
            raise ValueError("Input is truncated, changing or oversized")
        return data
    finally:
        os.close(fd)


def parser_module(kernel):
    path = kernel / "tools/aurora-sep/aurora-adt-extract.py"
    source = read_regular(path)
    if sha(source) != PARSER_SHA256:
        raise ValueError("Pinned read-only ADT parser hash differs")
    namespace = {"__name__": "_camera_adt", "__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)
    return namespace


def current_identity():
    compatibles = (DT / "compatible").read_bytes().split(b"\0")
    if b"apple,j716s" not in compatibles or b"apple,t6050" not in compatibles:
        raise ValueError("Current target is not J716s/T6050")
    for name, iboot in (("asahi,os-fw-version", "asahi,iboot2-version"),
                         ("asahi,system-fw-version", "asahi,iboot1-version")):
        if ((DT / "chosen" / name).read_bytes() != b"27.3 beta1\0" or
                (DT / "chosen" / iboot).read_bytes() != b"mBoot-20457.60.65.0.1\0"):
            raise ValueError("Current boot is not the exact pinned 26C5035r firmware pairing")
    serial = (DT / "serial-number").read_bytes().rstrip(b"\0")
    if not serial or any(byte < 0x21 or byte > 0x7e for byte in serial):
        raise ValueError("Current unit serial metadata is invalid")
    return {"board": "j716s", "model": "Mac17,8", "chip": "t6050", "macos_build": BUILD,
            "unit_serial_sha256": sha(serial),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "firmware_binding": "exact-label-and-mboot-pair", "current_restore_build_verified": False}


def handoff(raw, parser):
    root, used, _, _ = parser["parse_adt"](raw)
    nodes = {}
    for path, node in parser["walk"](root):
        if path in nodes:
            raise ValueError("ADT contains ambiguous node paths")
        nodes[path] = node

    def properties(path):
        if path not in nodes:
            raise ValueError("ADT lacks required camera node")
        found = {}
        for prop in nodes[path].props:
            if prop.name in found or prop.placeholder:
                raise ValueError("ADT contains duplicate or placeholder camera properties")
            found[prop.name] = prop.value
        return found

    isp = properties("/arm-io/isp0")
    dart = properties("/arm-io/dart-isp0")
    if isp.get("pre-loaded") != struct.pack("<I", 1) or "exclave-assigned" not in isp:
        raise ValueError("Camera is not the expected exclave-assigned preloaded ISP")
    if isp.get("sensor-type") != struct.pack("<I", 118) or isp.get("cam-connections-scheme") != struct.pack("<I", 16):
        raise ValueError("Camera platform metadata differs from J716s")
    if dart.get("exclave-sid") != struct.pack("<4I", 4, 5, 8, 9):
        raise ValueError("ISP exclave stream ownership differs")
    segments_raw = isp.get("segment-ranges", b"")
    if len(segments_raw) != 64:
        raise ValueError("ISP requires exactly two complete firmware segments")
    segments = [dict(zip(("phys", "iova", "remap", "size", "flags"), values))
                for values in struct.iter_unpack("<QQQII", segments_raw)]
    text, data = segments
    for seg in segments:
        if (not seg["phys"] or not seg["size"] or (seg["phys"] | seg["size"]) & 0x3fff or
                seg["phys"] + seg["size"] >= 1 << 64 or seg["iova"] + seg["size"] > 0x2000000 or
                seg["remap"] != (0x10000000000 | seg["iova"])):
            raise ValueError("Invalid ISP segment alignment, bounds or remap")
    if (text["iova"], text["size"], data["iova"], data["size"]) != (0, 0xc1c000, 0xc1c000, 0x13d0000):
        raise ValueError("ISP segment layout differs from the pinned 26C5035r handoff")
    if max(text["phys"], data["phys"]) < min(text["phys"] + text["size"], data["phys"] + data["size"]):
        raise ValueError("ISP physical firmware segments overlap")
    return {"adt_used_bytes": used, "segments": segments, "platform_id": 118,
            "cam_connections_scheme": 16, "exclave_assigned": True,
            "exclave_sids": [4, 5, 8, 9], "preloaded": True,
            "sensor_identity_proven": False, "coprocessor_quiescence_verified": False,
            "firmware_abi_qualified": False, "privacy_indicator_qualified": False}


def extract_setfile(daemon):
    if sha(daemon) != DAEMON_SHA256:
        raise ValueError("Camera daemon is not the observed exact 26C5035r source")
    found = []
    for offset in range(0, len(daemon) - 31, 0x1000):
        header = struct.unpack_from(">8I", daemon, offset)
        if header[0] != SETFILE_MAGIC:
            continue
        if (header[2:4] != (0, 0) or not header[4] or header[4] & 0xffffff or
                not header[5] or header[5] & 0xffff or header[6:] != (0, 0x3c00000) or
                offset + PADDED_SIZE > len(daemon)):
            raise ValueError("Malformed or truncated exact sensor setfile")
        found.append((offset, daemon[offset:offset + PADDED_SIZE]))
    if not found or any(blob != found[0][1] for _, blob in found):
        raise ValueError("No unambiguous byte-identical 2224 setfile copies")
    return found[0][1], [offset for offset, _ in found]


def system_source(root):
    result = subprocess.run(["findmnt", "--json", "--target", str(root), "--output", "SOURCE,FSTYPE,OPTIONS"],
                            capture_output=True, text=True, check=True, timeout=30)
    entries = json.loads(result.stdout).get("filesystems", [])
    if len(entries) != 1 or entries[0].get("source") != "/dev/nvme0n1p2" or "ro" not in entries[0].get("options", "").split(","):
        raise ValueError("System source is not a read-only mount of this Mac's retained macOS partition")
    version = read_regular(root / "System/Library/CoreServices/SystemVersion.plist")
    if plistlib.loads(version).get("ProductBuildVersion") != BUILD:
        raise ValueError("Retained System build is not 26C5035r")
    daemon = read_regular(root / "usr/sbin/appleh16camerad")
    return daemon, version


def save_new(output, files):
    # Same exclusive private staging convention as prepare-als-calibration.py.
    spec = importlib.util.spec_from_file_location("als_camera_staging", PROJECT / "scripts/prepare-als-calibration.py")
    als = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(als)
    als.save_new(output, files)


def prepare(output, system_root, kernel):
    if os.geteuid() != 0:
        raise ValueError("Live ADT acquisition requires root")
    identity = current_identity()
    parser = parser_module(kernel)
    host = parser["Host"]()
    raw = parser["read_device"](parser["check_binding"](None, host), host)
    camera = handoff(raw, parser)
    daemon, version = system_source(system_root)
    blob, offsets = extract_setfile(daemon)
    isp_status = (DT / "soc/isp@580000000/status").read_bytes()
    if isp_status != b"disabled\0":
        raise ValueError("ISP is not disabled; preparation does not authorize running camera hardware")
    report = {"schema": 1, **identity, "handoff": camera, "adt_sha256": sha(raw),
              "daemon_sha256": sha(daemon), "system_version_sha256": sha(version),
              "firmware": FIRMWARE, "firmware_sha256": sha(blob), "firmware_bytes": len(blob),
              "daemon_offsets": offsets, "candidate_sensor_id": "0x958", "setfile_magic": hex(SETFILE_MAGIC),
              "byte_for_byte": True, "installed": False, "sensor_identity_proven": False,
              "physically_qualified": False, "hardware_touched": False}
    save_new(output, {FIRMWARE: blob, "source/appleh16camerad": daemon,
                      "source/SystemVersion.plist": version, "source/adt.bin": raw, REPORT: encoded(report)})
    return {"prepared": True, "manifest_sha256": sha(encoded(report)), "sensor_identity_proven": False,
            "installed": False, "hardware_touched": False}


def check(package, report_sha256, kernel):
    report_raw = read_regular(package / REPORT, private=True)
    if sha(report_raw) != report_sha256:
        raise ValueError("Camera provenance differs from its external SHA256 anchor")
    report = json.loads(report_raw)
    identity = current_identity()
    if report.get("schema") != 1 or any(report.get(k) != v for k, v in identity.items()):
        raise ValueError("Camera package is not bound to this unit, exact firmware and current boot")
    raw = read_regular(package / "source/adt.bin", private=True)
    camera = handoff(raw, parser_module(kernel))
    daemon = read_regular(package / "source/appleh16camerad", private=True)
    version = read_regular(package / "source/SystemVersion.plist", private=True)
    blob, offsets = extract_setfile(daemon)
    if (sha(raw) != report.get("adt_sha256") or camera != report.get("handoff") or
            sha(version) != report.get("system_version_sha256") or plistlib.loads(version).get("ProductBuildVersion") != BUILD or
            sha(daemon) != report.get("daemon_sha256") or report.get("daemon_offsets") != offsets or
            read_regular(package / FIRMWARE, private=True) != blob or report.get("firmware_sha256") != sha(blob) or
            report.get("firmware_bytes") != len(blob) or report.get("firmware") != FIRMWARE or
            report.get("installed") is not False or report.get("sensor_identity_proven") is not False):
        raise ValueError("Camera package source evidence or candidate tuning differs")
    return {"checked": True, "sensor_identity_proven": False, "installed": False, "hardware_touched": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--system-root", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    checked = commands.add_parser("check")
    checked.add_argument("package", type=Path)
    checked.add_argument("--manifest-sha256", required=True)
    for command in (prep, checked):
        command.add_argument("--kernel", type=Path, default=PROJECT / "work/sources/kernel")
    args = parser.parse_args()
    try:
        report = prepare(args.output, args.system_root, args.kernel) if args.command == "prepare" else check(args.package, args.manifest_sha256, args.kernel)
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError) as error:
        parser.exit(1, f"Camera preparation refused: {error}\n")


if __name__ == "__main__":
    main()
