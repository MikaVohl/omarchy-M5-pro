#!/usr/bin/env python3
"""Inspect or preserve same-unit ALS CalibrationData; never install it or touch AOP.

On the target's paired macOS: capture PRIVATE_DIR (read-only hardware access,
explicit private artifact creation). On this J716s Linux: check PRIVATE_DIR,
then prepare PRIVATE_DIR --output PRIVATE_FIRMWARE_DIR. Check writes nothing.
Capture and preparation refuse existing output directories. Firmware is copied
byte-for-byte, not synthesized, calibrated, or qualified by this program.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
FIRMWARE = "apple/aop-als-cal.bin"
SERIAL_PATH = Path("/sys/firmware/devicetree/base/serial-number")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def text(value):
    if isinstance(value, bytes):
        value = value.rstrip(b"\0").decode("ascii")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid platform identity")
    return value.strip()


def platform_identity(data):
    tree = plistlib.loads(data)
    if not isinstance(tree, list) or len(tree) != 1 or not isinstance(tree[0], dict):
        raise ValueError("Expected one IOPlatformExpertDevice")
    node = tree[0]
    return text(node["model"]), text(node["IOPlatformSerialNumber"])


def calibration(data):
    """Search only the als registry subtree; refuse ambiguous factory blobs."""
    roots = plistlib.loads(data)
    if not isinstance(roots, list) or len(roots) != 1:
        raise ValueError("Expected exactly one als registry root")
    found = []

    def visit(node):
        if not isinstance(node, dict):
            raise ValueError("Invalid IORegistry node")
        if "CalibrationData" in node:
            blob = node["CalibrationData"]
            if not isinstance(blob, bytes) or not blob:
                raise ValueError("CalibrationData must be nonempty plist data")
            found.append(blob)
        children = node.get("IORegistryEntryChildren", [])
        if not isinstance(children, list):
            raise ValueError("Invalid IORegistry children")
        for child in children:
            visit(child)

    visit(roots[0])
    if len(found) != 1:
        raise ValueError(f"Expected one CalibrationData blob, found {len(found)}")
    return found[0]


def save_new(directory, files):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def capture(directory, profile):
    if platform.system() != "Darwin":
        raise ValueError("Capture must run on the target's paired macOS")
    model = subprocess.check_output(["sysctl", "-n", "hw.model"], text=True).strip()
    build = subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip()
    if (model, build) != (profile["model"], profile["macos_build"]):
        raise ValueError("macOS model/build differs from profiles/j716s.json")
    identity = subprocess.check_output(["ioreg", "-r", "-d", "1", "-a", "-c", "IOPlatformExpertDevice"], timeout=30)
    als = subprocess.check_output(["ioreg", "-r", "-a", "-n", "als", "-l"], timeout=30)
    actual_model, serial = platform_identity(identity)
    if actual_model != model:
        raise ValueError("IORegistry and sysctl disagree about target model")
    blob = calibration(als)
    report = {"schema": 1, "model": model, "board": profile["board"], "macos_build": build,
              "unit_serial_sha256": sha(serial.encode("ascii")),
              "ioreg_als_sha256": sha(als), "ioreg_platform_sha256": sha(identity),
              "firmware": FIRMWARE, "firmware_sha256": sha(blob), "firmware_size": len(blob),
              "source": "ioreg -r -a -n als -l / CalibrationData",
              "byte_for_byte": True, "physically_qualified": False}
    save_new(directory, {"als.plist": als, "platform.plist": identity,
                         "capture.json": json_bytes(report)})
    return report


def inspect(directory, profile, serial_path=SERIAL_PATH):
    report = json.loads((directory / "capture.json").read_text())
    als, identity = (directory / "als.plist").read_bytes(), (directory / "platform.plist").read_bytes()
    model, serial = platform_identity(identity)
    blob = calibration(als)
    expected = {"schema": 1, "model": profile["model"], "board": profile["board"],
                "macos_build": profile["macos_build"], "unit_serial_sha256": sha(serial.encode("ascii")),
                "ioreg_als_sha256": sha(als), "ioreg_platform_sha256": sha(identity),
                "firmware": FIRMWARE, "firmware_sha256": sha(blob), "firmware_size": len(blob)}
    if model != profile["model"] or any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Capture identity, hashes or pinned build do not match")
    target_serial = text(serial_path.read_bytes())
    if sha(target_serial.encode("ascii")) != expected["unit_serial_sha256"]:
        raise ValueError("Calibration is from a different physical unit")
    return {**expected, "byte_for_byte": True, "target_unit_matches": True,
            "physically_qualified": False, "installed": False}, blob


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    cap = commands.add_parser("capture", help="macOS: collect private plist evidence; no hardware writes")
    cap.add_argument("directory", type=Path)
    for name in ("check", "prepare"):
        command = commands.add_parser(name, help="validate same-unit capture" if name == "check" else "create private firmware staging directory")
        command.add_argument("directory", type=Path)
        command.add_argument("--target-serial-file", type=Path, default=SERIAL_PATH,
                             help="raw target DT serial-number file, defaults to this Linux unit; never a board-wide override")
        if name == "prepare":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    try:
        if args.command == "capture":
            report = capture(args.directory, profile)
        else:
            if args.target_serial_file == SERIAL_PATH:
                compatible = Path("/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
                if b"apple,j716s" not in compatible:
                    raise ValueError("Current Linux target is not J716s")
            report, blob = inspect(args.directory, profile, args.target_serial_file)
            if args.command == "prepare":
                save_new(args.output, {FIRMWARE: blob, "als-calibration-manifest.json": json_bytes(report)})
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException, subprocess.SubprocessError) as error:
        parser.exit(1, f"ALS preparation refused: {error}\n")


if __name__ == "__main__":
    main()
