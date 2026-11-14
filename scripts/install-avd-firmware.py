#!/usr/bin/env python3
"""Install a verified J716s AVD package without loading hardware or rebooting.

The next AVD probe can start unqualified hardware. Preserve this deployment
receipt and use --rollback to remove only the exact file installed by this tool.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("avd_package", PROJECT / "scripts/prepare-avd-firmware.py")
avd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avd)
TARGET = Path("/usr/lib/firmware") / avd.FIRMWARE_NAME
RECEIPT = Path("/etc/j716s/avd-firmware.json")


def trusted(path):
    if path.resolve() != path or path.is_symlink() or not path.is_file():
        raise ValueError("Unsafe installed file: " + str(path))
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ValueError("Installed file must be root-owned and not writable by others")


def atomic(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    pending = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        pending.unlink(missing_ok=True)


def preflight():
    if os.geteuid() != 0:
        raise ValueError("Run through sudo or pkexec")
    if b"apple,j716s" not in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"):
        raise ValueError("Not the J716s board")
    guard = Path("/usr/local/lib/j716s/j716s-bind-boot.py")
    trusted(guard)
    subprocess.run([sys.executable, "-B", str(guard), "--check-only"], check=True)
    identity = json.loads(Path("/usr/share/j716s/image-identity.json").read_text())
    if identity["firmware_build"] != "26C5035r":
        raise ValueError("Not the exact firmware build")
    for directory in (TARGET.parent, RECEIPT.parent):
        if directory.resolve() != directory or directory.is_symlink() or not directory.is_dir():
            raise ValueError("Unsafe installed directory: " + str(directory))
    return identity


def install(package, apply):
    preflight()
    image, report = avd.checked_package(package)
    if TARGET.exists() or TARGET.is_symlink() or RECEIPT.exists() or RECEIPT.is_symlink():
        raise ValueError("Existing AVD firmware/receipt: inspect or roll back, never overwrite")
    if not apply:
        print("CHECK ONLY: exact-profile firmware package and live boot binding passed; no hardware or files changed.")
        return
    record = {"schema": 1, "path": str(TARGET), "sha256": avd.sha(image), "bytes": len(image),
              "running_kernel": os.uname().release, "package": report, "physical_qualification": "not performed"}
    # Receipt precedes publication; interrupted installation is removable by rollback.
    atomic(RECEIPT, (json.dumps(record, indent=2) + "\n").encode(), 0o600)
    atomic(TARGET, image, 0o644)
    trusted(TARGET)
    if TARGET.read_bytes() != image:
        raise ValueError("Installed firmware readback differs")
    print("INSTALLED: hash-bound AVD v5/tier2 firmware; no module load or reboot. Hardware qualification still required.")


def rollback():
    preflight()
    trusted(RECEIPT)
    record = json.loads(RECEIPT.read_text())
    if record["schema"] != 1 or record["path"] != str(TARGET):
        raise ValueError("Unsupported AVD deployment receipt")
    if TARGET.exists() or TARGET.is_symlink():
        trusted(TARGET)
        data = TARGET.read_bytes()
        if len(data) != record["bytes"] or avd.sha(data) != record["sha256"]:
            raise ValueError("Firmware changed since installation; refusing to remove someone else's bytes")
        TARGET.unlink()
    RECEIPT.unlink()
    for directory in (TARGET.parent, RECEIPT.parent):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    print("ROLLED BACK: only this tool's AVD firmware and receipt removed; loaded driver unchanged.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--install", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    parser.add_argument("--package", type=Path, default=PROJECT / "outputs/firmware/j716s-26C5035r-avd")
    args = parser.parse_args()
    rollback() if args.rollback else install(args.package, args.install)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
