#!/usr/bin/env python3
"""Preserve same-unit ALS evidence and install only receipt-bound CalibrationData.

capture runs on paired macOS; check/prepare bind its plists to this Linux unit.
recover-factory privately preserves the same-unit HmCA factory record, NOT an
installable CalibrationData blob. check-install is read-only; install and rollback
change only the firmware file and their receipt. They never touch AOP, load a
module, change the device tree, select a boot, or reboot. Keep manifests private.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import stat
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
FIRMWARE = "apple/aop-als-cal.bin"
SERIAL_PATH = Path("/sys/firmware/devicetree/base/serial-number")
TARGET = Path("/usr/lib/firmware") / FIRMWARE
RECEIPT = Path("/etc/j716s/als-calibration.json")
MANIFEST = "als-calibration-manifest.json"
MAX_INPUT = 4 * 1024 * 1024


def private_read(path):
    if ".." in Path(path).parts:
        raise ValueError("Calibration input traversal is forbidden")
    path = Path(os.path.abspath(path))
    if path.resolve() != path:
        raise ValueError("Calibration input must not contain symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        owners = {os.geteuid()}
        if os.geteuid() == 0 and os.environ.get("SUDO_UID", "").isdigit():
            owners.add(int(os.environ["SUDO_UID"]))
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in owners or info.st_mode & 0o077:
            raise ValueError("Calibration input must be a private single-link regular file")
        if not 0 < info.st_size <= MAX_INPUT:
            raise ValueError("Calibration input exceeds its size bound")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(MAX_INPUT + 1)
        if len(data) != info.st_size:
            raise ValueError("Calibration input changed or is oversized")
        return data
    finally:
        os.close(fd)


def runtime_preflight():
    if os.geteuid() != 0:
        raise ValueError("Installation requires sudo or pkexec")
    dt = SERIAL_PATH.parent
    compatibles = (dt / "compatible").read_bytes().split(b"\0")
    if b"apple,j716s" not in compatibles or b"apple,t6050" not in compatibles:
        raise ValueError("Current Linux target is not J716s/T6050")
    for version, iboot in (("asahi,os-fw-version", "asahi,iboot2-version"),
                           ("asahi,system-fw-version", "asahi,iboot1-version")):
        if ((dt / "chosen" / version).read_bytes() != b"27.3 beta1\0" or
                (dt / "chosen" / iboot).read_bytes() != b"mBoot-20457.60.65.0.1\0"):
            raise ValueError("Current boot is not the pinned 26C5035r firmware pairing")
    guard = Path("/usr/local/lib/j716s/j716s-bind-boot.py")
    trusted_runtime(guard)
    subprocess.run([sys.executable, "-B", str(guard), "--check-only"], check=True,
                   stdout=subprocess.DEVNULL)
    for directory in (TARGET.parent, RECEIPT.parent):
        trusted_runtime(directory, directory=True)


def trusted_runtime(path, directory=False):
    if path.resolve() != path:
        raise ValueError("Runtime calibration path contains a symlink")
    for ancestor in (*reversed(path.parents), path):
        info = ancestor.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Runtime calibration path is not root controlled")
    info = path.lstat()
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise ValueError("Invalid runtime calibration file type")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def stage_new(path, data):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    pending = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return pending
    except BaseException:
        pending.unlink(missing_ok=True)
        raise


def publish_new(path, data):
    pending = stage_new(path, data)
    try:
        # Hard-link publication is atomic and cannot overwrite a concurrent file.
        os.link(pending, path, follow_symlinks=False)
    finally:
        pending.unlink(missing_ok=True)
        sync_directory(path.parent)


def checked_package(directory, profile, manifest_sha256):
    raw = private_read(directory / MANIFEST)
    if sha(raw) != manifest_sha256:
        raise ValueError("Prepared calibration manifest differs from its external SHA256 anchor")
    report, blob = inspect_files(private_read(directory / "capture.json"),
                                 private_read(directory / "als.plist"),
                                 private_read(directory / "platform.plist"), profile, SERIAL_PATH)
    if json.loads(raw) != report or private_read(directory / FIRMWARE) != blob:
        raise ValueError("Prepared calibration or its source evidence differs")
    return report, blob


def install(directory, profile, manifest_sha256, apply):
    runtime_preflight()
    report, blob = checked_package(directory, profile, manifest_sha256)
    if any(path.exists() or path.is_symlink() for path in (TARGET, RECEIPT)):
        raise ValueError("Existing calibration or receipt; never overwrite")
    if not apply:
        return {"check_only": True, "target_unit_matches": True, "installed": False}
    record = {"schema": 1, "path": str(TARGET), "sha256": sha(blob), "bytes": len(blob),
              "manifest_sha256": manifest_sha256, "package": report,
              "running_kernel": os.uname().release, "physically_qualified": False,
              "aop_touched": False}
    pending = stage_new(TARGET, blob)
    try:
        info = pending.stat()
        record.update({"firmware_device": info.st_dev, "firmware_inode": info.st_ino})
        # Receipt first makes interrupted publication removable. The reserved
        # inode also prevents rollback from deleting a concurrent writer's file.
        publish_new(RECEIPT, json_bytes(record))
        os.link(pending, TARGET, follow_symlinks=False)
    finally:
        pending.unlink(missing_ok=True)
        sync_directory(TARGET.parent)
    trusted_runtime(TARGET)
    if private_read(TARGET) != blob:
        raise ValueError("Installed calibration readback differs")
    return {"installed": True, "aop_touched": False, "physically_qualified": False}


def rollback():
    runtime_preflight()
    trusted_runtime(RECEIPT)
    record = json.loads(private_read(RECEIPT))
    if record.get("schema") != 1 or record.get("path") != str(TARGET):
        raise ValueError("Unsupported ALS calibration receipt")
    if record.get("package", {}).get("unit_serial_sha256") != sha(text(SERIAL_PATH.read_bytes()).encode("ascii")):
        raise ValueError("Receipt belongs to a different physical unit")
    if TARGET.exists() or TARGET.is_symlink():
        trusted_runtime(TARGET)
        info = TARGET.lstat()
        if (info.st_dev, info.st_ino) != (record.get("firmware_device"), record.get("firmware_inode")):
            raise ValueError("Calibration publication inode changed; refusing to remove someone else's file")
        blob = private_read(TARGET)
        if len(blob) != record.get("bytes") or sha(blob) != record.get("sha256"):
            raise ValueError("Calibration changed; refusing to remove someone else's bytes")
        TARGET.unlink()
        sync_directory(TARGET.parent)
    RECEIPT.unlink()
    sync_directory(RECEIPT.parent)
    return {"rolled_back": True, "aop_touched": False}


def recover_factory(output, kernel, device, partition_uuid):
    spec = importlib.util.spec_from_file_location("sensor_factory", PROJECT / "scripts/prepare-sensor-firmware.py")
    sensor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sensor)
    factory, extractor = sensor.pinned_tools(kernel)
    identity, binding = sensor.current_identity(factory, extractor)
    fd, partition = sensor.device_binding(device, partition_uuid)
    try:
        records, checkpoint = sensor.read_factory(fd, partition["size"], factory)
    finally:
        os.close(fd)
    combined, original = factory["admit"](records, identity, b"HmCA")
    admitted = sensor.validate_container(combined, b"HmCA", identity, factory, extractor)
    name = original.pop("source")
    report = {"schema": 1, "board": "j716s", "model": "Mac17,8", "macos_build": "26C5035r",
              "boot_binding": binding, "partition": partition, "checkpoint": checkpoint,
              "record": admitted, "original_record": original,
              "source_img4_sha256": sha(records[name]), "source_img4_bytes": len(records[name]),
              "installable_calibration": False, "calibrationdata_equivalence_proven": False,
              "reason": "HmCA factory envelope is not macOS ALS IORegistry CalibrationData"}
    save_new(output, {"factory/HmCA.img4": records[name], "factory/HmCA.comb": combined,
                      "factory-recovery.json": json_bytes(report)})
    return {"prepared": True, "manifest_sha256": sha(json_bytes(report)),
            "installable_calibration": False, "calibrationdata_equivalence_proven": False,
            "hardware_touched": False}


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
    return inspect_files((directory / "capture.json").read_bytes(),
                         (directory / "als.plist").read_bytes(),
                         (directory / "platform.plist").read_bytes(), profile, serial_path)


def inspect_files(raw_report, als, identity, profile, serial_path):
    report = json.loads(raw_report)
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
    recovery = commands.add_parser("recover-factory", help="preserve same-unit HmCA privately; never install as CalibrationData")
    recovery.add_argument("--output", type=Path, required=True)
    recovery.add_argument("--kernel", type=Path, default=PROJECT / "work/sources/kernel")
    recovery.add_argument("--device", type=Path, required=True)
    recovery.add_argument("--partition-uuid", required=True)
    for name in ("check-install", "install"):
        command = commands.add_parser(name, help="validate receipt-bound package" if name == "check-install" else "publish private firmware and receipt only; does not activate ALS")
        command.add_argument("directory", type=Path)
        command.add_argument("--manifest-sha256", required=True)
    commands.add_parser("rollback", help="remove only this receipt's unchanged calibration; no hardware access")
    args = parser.parse_args()
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    try:
        if args.command == "capture":
            report = capture(args.directory, profile)
        elif args.command == "recover-factory":
            report = recover_factory(args.output, args.kernel, args.device, args.partition_uuid)
        elif args.command in ("check-install", "install"):
            report = install(args.directory, profile, args.manifest_sha256, args.command == "install")
        elif args.command == "rollback":
            report = rollback()
        else:
            if args.target_serial_file == SERIAL_PATH:
                compatible = Path("/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
                if b"apple,j716s" not in compatible:
                    raise ValueError("Current Linux target is not J716s")
            report, blob = inspect(args.directory, profile, args.target_serial_file)
            if args.command == "prepare":
                save_new(args.output, {FIRMWARE: blob, MANIFEST: json_bytes(report),
                                      **{name: (args.directory / name).read_bytes()
                                         for name in ("als.plist", "platform.plist", "capture.json")}})
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException, subprocess.SubprocessError) as error:
        parser.exit(1, f"ALS preparation refused: {error}\n")


if __name__ == "__main__":
    main()
