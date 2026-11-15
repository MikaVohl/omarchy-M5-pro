#!/usr/bin/env python3
"""Acquire private, exact-unit media registry/metadata in paired 26C5035r macOS.

Never starts an encoder/ANE, opens a model/video session, loads a driver, reads
MMIO/firmware RAM, installs APRI, or changes boot state. Registry snapshots do
NOT prove DMA completion, reset, command ABI or power sequencing. Run capture
in the physical unit's paired macOS, then check the externally hash-anchored
bundle on Linux. This is acquisition, not permission for a first hardware job.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
REPORT = "media-macos-acquisition.json"
BUILD = "26C5035r"
QUERIES = (
    ("apr0-adt.plist", ("-p", "IODeviceTree", "-n", "apr0")),
    ("apr0-service.plist", ("-n", "apr0")),
    ("dart-apr0-adt.plist", ("-p", "IODeviceTree", "-n", "dart-apr0")),
    ("ave0-adt.plist", ("-p", "IODeviceTree", "-n", "ave0")),
    ("ave0-service.plist", ("-n", "ave0")),
    ("ane-adt.plist", ("-p", "IODeviceTree", "-n", "ane")),
    ("ane-service.plist", ("-n", "ane")),
)
UNQUALIFIED = {"first_dma_authorized": False, "engine_started": False,
               "firmware_installed": False, "reset_qualified": False,
               "firmware_abi_qualified": False}
MISSING = {
    "prores": ["J716s 627 native first-job MMIO/descriptor and statistics-capacity trace",
               "qualified SCODEC/APR/DART quiescence and translation-preservation contract",
               "exact-board one-shot boot/DTB/module identity, physical power-cycle fallback",
               "one 320x240 P216 Standard frame: normal DONE, software-decode dimensions/chroma/pixel error"],
    "ave": ["9013.55.1 exact live TEXT CRC and generation-specific boot/hello/ring contract",
            "26C5035r H264/HEVC Config/Open/Start/Process/reply layouts and native field ledger",
            "Complete/Flush/Stop/Close/Halt and DMA-stop witness; native bounded session trace"],
    "ane": ["live reserved TEXT SHA256, 0xec000/0x280000 geometry and raw-no-XNU cold-state evidence",
            "26C5035r DATA relocations/patchbay, RVBAR, boot arguments, heap span and command-pool separation",
            "J716s PWGATE/DART/DAPF and native cold-boot/first-program/clean-stop trace",
            "exact-build compiler HWX/task/transport ABI and numerical reference result"],
}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prores = load("media_prores_input", "prepare-prores-firmware.py")
als = load("media_unit_identity", "prepare-als-calibration.py")


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def registry_roots(data):
    roots = plistlib.loads(data) if data else []
    if not isinstance(roots, list) or any(not isinstance(root, dict) for root in roots):
        raise ValueError("media registry source is not a plist root list")
    return len(roots)


def regular_system_file(path, maximum=65536):
    parent, name = prores.open_parent(path)
    try:
        fd = os.open(name, prores.FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise ValueError("media System metadata is not a bounded regular file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) != info.st_size:
            raise ValueError("media System metadata changed while read")
        return data
    finally:
        os.close(fd)


def save_new(output, files):
    # Fixed flat filenames; descriptor-relative exclusive creation and no-follow
    # traversal match the ProRes private-package convention.
    parent, name = prores.open_parent(output)
    directory = None
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        directory = os.open(name, prores.FLAGS | os.O_DIRECTORY, dir_fd=parent)
        for filename, data in files.items():
            prores.write_new(directory, filename, data)
        os.fsync(directory)
        os.fsync(parent)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def capture(output, unit_sha256):
    prores.profile()
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    identity, platform = als.macos_identity(profile, unit_sha256)
    files = {"platform.plist": platform}
    observations = []
    for filename, selector in QUERIES:
        command = ["/usr/sbin/ioreg", "-r", "-a", "-l", *selector]
        data = subprocess.check_output(command, timeout=30)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("media registry source exceeds its bound")
        roots = registry_roots(data)
        files[filename] = data
        observations.append({"file": filename, "command": command, "roots": roots,
                             "available": roots > 0})
    version = regular_system_file(Path("/System/Library/CoreServices/SystemVersion.plist"))
    if plistlib.loads(version).get("ProductBuildVersion") != BUILD:
        raise ValueError("paired macOS build changed during acquisition")
    files["SystemVersion.plist"] = version
    extensions = Path("/System/Library/Extensions")
    for path in sorted(extensions.iterdir()):
        if path.suffix != ".kext" or not any(word in path.name.lower() for word in ("prores", "appleave", "appleane")):
            continue
        data = regular_system_file(path / "Contents/Info.plist")
        if not isinstance(plistlib.loads(data), dict):
            raise ValueError("media kext metadata is not a plist dictionary")
        files["kext-" + path.name + ".plist"] = data
    if "kext-AppleProResHW.kext.plist" not in files or str(plistlib.loads(
            files["kext-AppleProResHW.kext.plist"]).get("CFBundleVersion")) != "627":
        raise ValueError("paired AppleProResHW 627 metadata is missing or differs")
    report = {**identity, "kind": "paired-macos-read-only-media-acquisition", **UNQUALIFIED,
              "kernel_commit": prores.KERNEL_COMMIT, "observations": observations,
              "missing_contract": MISSING,
              "files": {name: {"bytes": len(data), "sha256": prores.sha(data)} for name, data in files.items()}}
    raw = encoded(report)
    save_new(output, {**files, REPORT: raw})
    return {"manifest_sha256": prores.sha(raw), "missing_contract": MISSING, **UNQUALIFIED}


def check(package, anchor, unit_sha256):
    prores.profile()
    raw = prores.private_input(package / REPORT, 65536)
    if prores.sha(raw) != anchor:
        raise ValueError("media acquisition differs from its external SHA256 anchor")
    report = json.loads(raw)
    if (not isinstance(report, dict) or report.get("schema") != 1 or
            report.get("kind") != "paired-macos-read-only-media-acquisition" or
            (report.get("model"), report.get("board"), report.get("macos_build"), report.get("unit_serial_sha256")) !=
            ("Mac17,8", "j716s", BUILD, unit_sha256) or report.get("kernel_commit") != prores.KERNEL_COMMIT or
            report.get("missing_contract") != MISSING or
            any(report.get(key) is not value for key, value in UNQUALIFIED.items())):
        raise ValueError("media acquisition identity or qualification claims differ")
    records = report.get("files")
    required = {"platform.plist", "SystemVersion.plist", "kext-AppleProResHW.kext.plist", *(name for name, _ in QUERIES)}
    if not isinstance(records, dict) or not required.issubset(records):
        raise ValueError("media acquisition lacks targeted source evidence")
    files = {}
    for name, record in records.items():
        if name not in required and not re.fullmatch(r"kext-[A-Za-z0-9_.-]+\.kext\.plist", name):
            raise ValueError("unexpected media acquisition filename")
        data = prores.private_input(package / name, 16 * 1024 * 1024)
        if record != {"bytes": len(data), "sha256": prores.sha(data)}:
            raise ValueError("media acquisition source integrity differs")
        files[name] = data
    model, serial = als.platform_identity(files["platform.plist"])
    if model != "Mac17,8" or prores.sha(serial.encode("ascii")) != unit_sha256:
        raise ValueError("media acquisition is from another physical unit")
    if (report.get("ioreg_platform_sha256") != prores.sha(files["platform.plist"]) or
            plistlib.loads(files["SystemVersion.plist"]).get("ProductBuildVersion") != BUILD or
            str(plistlib.loads(files["kext-AppleProResHW.kext.plist"]).get("CFBundleVersion")) != "627"):
        raise ValueError("media acquisition build or kext version differs")
    observations = []
    for name, selector in QUERIES:
        roots = registry_roots(files[name])
        observations.append({"file": name, "command": ["/usr/sbin/ioreg", "-r", "-a", "-l", *selector],
                             "roots": roots, "available": roots > 0})
    if report.get("observations") != observations:
        raise ValueError("media acquisition registry availability claims differ")
    return {"checked": True, "missing_contract": MISSING, **UNQUALIFIED}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--capture", type=Path, help="new private output directory in paired macOS")
    action.add_argument("--check", type=Path, help="private bundle after returning to Linux")
    parser.add_argument("--unit-serial-sha256", required=True, help="external SHA256 of this Linux unit's serial")
    parser.add_argument("--manifest-sha256", help="external manifest anchor required for --check")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", args.unit_serial_sha256):
            raise ValueError("expected an external unit SHA256")
        if args.check:
            if not args.manifest_sha256 or not re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256):
                raise ValueError("check requires an external manifest SHA256")
            result = check(args.check, args.manifest_sha256, args.unit_serial_sha256)
        else:
            if args.manifest_sha256:
                raise ValueError("capture does not accept a manifest anchor")
            result = capture(args.capture, args.unit_serial_sha256)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "Refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()
