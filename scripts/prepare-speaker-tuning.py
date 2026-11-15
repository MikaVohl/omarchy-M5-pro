#!/usr/bin/env python3
"""Collect/verify exact-unit J716s speaker tuning; never install or enable audio.

Uses a hash-pinned upstream collector in a disposable source directory. Capture
requires the retained macOS System volume already mounted read-only. Outputs
contain private factory calibration; keep them out of source publication.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
COMMIT = "e8f0711f8f77cc5fdaf3f78348321d865a3818bc"
SOURCE_URL = "https://codeload.github.com/aurora-silicon/aurora-installer/tar.gz/" + COMMIT
SOURCE_SHA256 = "51b6c0f179d98a38ac1e1a1261eb1d94c3956742969eed278ef740fa67bd3c16"
PREFIX = "aurora-installer-" + COMMIT
BUNDLE = "speaker-tuning.tar.gz"
REPORT = "speaker-preparation.json"
DT = Path("/proc/device-tree")


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular, non-symlink file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def collector(archive, destination):
    # Hash the same open descriptor used by the archive parser.
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("source archive must be a regular file")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("collector source archive differs from the pinned revision")
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as tar:
            selected = []
            seen = set()
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != PREFIX or ".." in parts or member.name.startswith("/"):
                    raise ValueError("unsafe source archive path")
                if not (member.name.startswith(PREFIX + "/asahi_firmware/") or
                        member.name == PREFIX + "/tools/speaker-tuning/speaker_tuning.py"):
                    continue
                if not (member.isdir() or member.isfile()) or member.name in seen:
                    raise ValueError("unsafe or duplicate collector source member")
                seen.add(member.name)
                selected.append(member)
            tar.extractall(destination, members=selected, filter="data")
    path = destination / PREFIX / "tools/speaker-tuning/speaker_tuning.py"
    spec = importlib.util.spec_from_file_location("j716s_pinned_speaker_collector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit_id(dt):
    raw = (dt / "serial-number").read_bytes().rstrip(b"\0")
    if not 1 <= len(raw) <= 64 or any(c < 33 or c > 126 for c in raw):
        raise ValueError("valid running-unit identity is required")
    return hashlib.sha256(b"aurora-speaker-unit-v1\0" + raw).hexdigest()


def admit_manifest(meta, dt=DT):
    compatible = (dt / "compatible").read_bytes().split(b"\0")
    if compatible[0] != b"apple,j716s" or b"apple,t6050" not in compatible:
        raise ValueError("running board is not J716s/T6050")
    spid = (dt / "chosen/asahi,speaker-id").read_bytes()
    if len(spid) != 4 or int.from_bytes(spid, "big") not in (0, 3):
        raise ValueError("running speaker vendor strap is unknown")
    if (meta.get("schema"), meta.get("complete"), meta.get("aid"),
            meta.get("acoustic_id"), meta.get("board"), meta.get("macos_build")) != (
            2, True, "AID41", 41, "j716s", "26C5035r"):
        raise ValueError("tuning identity differs from J716s/AID41/26C5035r")
    if meta.get("unit_id") != unit_id(dt):
        raise ValueError("factory tuning belongs to another or unidentified unit")
    if meta.get("speaker_id") != int.from_bytes(spid, "big"):
        raise ValueError("tuning speaker vendor differs from the running strap")
    calibration = meta.get("speaker_thiele_small")
    if not isinstance(calibration, dict) or calibration.get("bytes") != 138:
        raise ValueError("the six-speaker factory calibration record is required")
    if not meta.get("lsm2_mask_lattice"):
        raise ValueError("complete upstream collection needs the AudioDSP lattice")


def checked_bundle(module, path):
    try:
        with module.opened_bundle(path) as (bundle, _):
            meta = json.loads(bundle[module.MANIFEST])
            admit_manifest(meta)
            return meta
    except module.Refused as error:
        raise ValueError(str(error)) from error


def readonly_system(root):
    mount = json.loads(subprocess.check_output(
        ["findmnt", "--json", "--target", str(root), "--output", "TARGET,OPTIONS"], text=True))
    filesystems = mount.get("filesystems", [])
    if len(filesystems) != 1 or "ro" not in filesystems[0]["options"].split(","):
        raise ValueError("macOS System must already be mounted read-only")
    version = plistlib.loads((root / "System/Library/CoreServices/SystemVersion.plist").read_bytes())
    if version.get("ProductBuildVersion") != "26C5035r":
        raise ValueError("retained System is not the exact paired macOS build")


def capture(module, root, output):
    readonly_system(root)
    # Identity must be available before any collection; no open-loop fallback.
    unit_id(DT)
    if output.exists() or output.is_symlink():
        raise ValueError("refusing existing output")
    output.mkdir(mode=0o700)
    # Failed capture retains an incomplete private directory, never a receipt.
    path = output / BUNDLE
    try:
        module.collect_linux(str(root), out=path)
    except module.Refused as error:
        raise ValueError(str(error)) from error
    meta = checked_bundle(module, path)
    report = {"schema": 1, "recipe": "j716s-factory-speaker-tuning-v1",
              "source": {"commit": COMMIT, "url": SOURCE_URL, "sha256": SOURCE_SHA256},
              "board": "j716s", "aid": "AID41", "macos_build": "26C5035r",
              "speaker_id": meta["speaker_id"], "source_file_count": meta["source_file_count"],
              "same_unit_calibration": True, "bundle_sha256": digest(path),
              "bundle_bytes": path.stat().st_size,
              "qualification": "collected-not-playback-qualified",
              "audio_enabled": False, "installed": False}
    with (output / REPORT).open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"PREPARED: {meta['source_file_count']} AID41 files, same-unit calibration, vendor {meta['speaker_id']}.")
    print(f"Private package: {output}. No daemon start, installation, playback or boot change.")


def verify(module, output):
    if output.is_symlink() or not output.is_dir():
        raise ValueError("package must be a real directory")
    report_path = output / REPORT
    digest(report_path)
    report = json.loads(report_path.read_text())
    path = output / BUNDLE
    if (report.get("schema"), report.get("recipe"), report.get("source")) != (
            1, "j716s-factory-speaker-tuning-v1",
            {"commit": COMMIT, "url": SOURCE_URL, "sha256": SOURCE_SHA256}):
        raise ValueError("speaker preparation receipt provenance differs")
    if report.get("bundle_sha256") != digest(path) or report.get("bundle_bytes") != path.stat().st_size:
        raise ValueError("speaker bundle changed after preparation")
    meta = checked_bundle(module, path)
    print(f"VERIFIED: complete {meta['aid']} bundle for this unit, macOS {meta['macos_build']}.")
    print("Preparation only: thermal-model parity, sense wiring and playback are not qualified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True, help=SOURCE_URL)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("capture")
    collect.add_argument("--macos-root", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="j716s-speaker-source-") as temp:
            module = collector(args.source_archive, Path(temp))
            if args.command == "capture":
                capture(module, args.macos_root, args.output)
            else:
                verify(module, args.package)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
