#!/usr/bin/env python3
"""Build RAM recovery and persistent-root initramfs; bind a real target ESP UUID.

Writes publication workspace artifacts only. Does not install, mount, enroll,
boot, or write target storage. The qualified filename denotes the historical
source recipe: fresh bytes require physical qualification before deployment.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import uuid

PROJECT = Path(__file__).resolve().parents[1]
BOOT = PROJECT / "outputs/boot-candidate"
ALPINE_URL = "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/aarch64/alpine-minirootfs-3.21.3-aarch64.tar.gz"
ALPINE_SHA256 = "ead8a4b37867bd19e7417dd078748e2312c0aea364403d96758d63ea8ff261ea"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_alpine(destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Refusing existing Alpine source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="j716s-alpine-", dir=destination.parent) as temporary:
        candidate = Path(temporary) / "source.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(ALPINE_URL, timeout=60) as response, candidate.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != ALPINE_SHA256:
            raise ValueError("Alpine source differs from pinned SHA-256")
        candidate.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esp-partuuid", required=True, help="actual discovered target ESP GPT PARTUUID; never a sample UUID")
    parser.add_argument("--firmware", type=Path, default=PROJECT / "outputs/firmware/j716s-26C5035r-linux")
    parser.add_argument("--alpine", type=Path, default=PROJECT / "work/j716s-build/rescue/alpine-minirootfs-3.21.3-aarch64.tar.gz")
    parser.add_argument("--fetch-alpine", action="store_true", help="fetch the exact public Alpine source if it is absent")
    parser.add_argument("--desktop-root", type=Path, help="optional completed ARM64 root tree; copies only the RAM desktop closure")
    parser.add_argument("--fdtput", default="fdtput", help="host fdtput from device-tree-compiler/dtc")
    parser.add_argument("--readelf", default="readelf", help="host readelf from binutils for optional desktop closure")
    args = parser.parse_args()
    esp = uuid.UUID(args.esp_partuuid)
    if esp.int == 0:
        raise ValueError("Zero is not an actual ESP PARTUUID")
    composer = load("j716s_rescue_composer", PROJECT / "work/aura-audit/compose-linux-rescue.py")
    initramfs = load("j716s_initramfs", PROJECT / "outputs/persistent-install/build-root-initramfs.py")
    composer.build_manifests()
    outputs = [BOOT / "j716s-rescue-qualified-kis.bin", BOOT / "t6050-j716s-kis02.dtb",
               BOOT / "j716s-rescue-initramfs.cpio.gz", BOOT / "j716s-root-initramfs.cpio.gz"]
    for output in outputs:
        if output.exists() or output.is_symlink() or output.with_suffix(output.suffix + ".json").exists():
            raise ValueError("Refusing existing generated artifact: " + str(output))
    if not args.alpine.exists():
        if not args.fetch_alpine:
            raise ValueError("Supply the pinned Alpine archive with --alpine or use --fetch-alpine")
        fetch_alpine(args.alpine)
    desktop = None
    if args.desktop_root is not None:
        directory = PROJECT / "work/aura-audit/ram-desktop"
        subprocess.run([sys.executable, str(PROJECT / "work/aura-audit/build-ram-probe-20261113.py"),
                        "--source-root", str(args.desktop_root), "--output", str(directory),
                        "--readelf", args.readelf], check=True)
        desktop = directory / "desktop-probe.tar.gz"
    initramfs.build(args.alpine, args.firmware, BOOT / "j716s-rescue-initramfs.cpio.gz", kind="rescue", desktop=desktop)
    initramfs.build(args.alpine, args.firmware, BOOT / "j716s-root-initramfs.cpio.gz", kind="root")
    composer.prepare_dtb(args.fdtput)
    report = composer.compose(str(esp))
    source = {"schema": 1, "url": ALPINE_URL, "sha256": ALPINE_SHA256,
              "architecture": "aarch64", "release": "3.21.3",
              "builder": "outputs/persistent-install/build-root-initramfs.py"}
    (BOOT / "rescue-source.json").write_text(json.dumps(source, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
