#!/usr/bin/env python3
"""Build the persistent-root initramfs from the already verified rescue source.

Only workspace files are written. No disks, mounts, enrollment or invented GPT
identities are involved; actual target UUIDs are supplied in the final payload.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import struct
import tarfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
ALPINE_SHA256 = "ead8a4b37867bd19e7417dd078748e2312c0aea364403d96758d63ea8ff261ea"
FIRMWARE_BUILD = "26C5035r"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe archive path: " + name)
    return str(path).removeprefix("./").rstrip("/")


def newc(entries):
    output = io.BytesIO()
    for inode, (name, mode, uid, gid, major, minor, data) in enumerate(entries, 1):
        encoded = name.encode() + b"\0"
        values = (inode, mode, uid, gid, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
        if any(not 0 <= value <= 0xFFFFFFFF for value in values):
            raise ValueError("newc field exceeds 32 bits")
        output.write(b"070701" + b"".join(f"{value:08x}".encode() for value in values))
        output.write(encoded)
        output.write(b"\0" * (-output.tell() % 4))
        output.write(data)
        output.write(b"\0" * (-output.tell() % 4))
    return output.getvalue()


def build(archive, firmware, output, kind="root", replace=False, desktop=None):
    if kind not in ("root", "rescue"):
        raise ValueError("Unknown initramfs kind")
    if output.exists() or output.with_suffix(output.suffix + ".json").exists():
        allowed = PROJECT / "outputs/boot-candidate" / ("j716s-" + kind + "-initramfs.cpio.gz")
        if not replace or output.resolve() != allowed.resolve() or output.is_symlink():
            raise ValueError("Refusing existing output; choose a fresh path")
    if output.is_symlink() or output.with_suffix(output.suffix + ".json").is_symlink():
        raise ValueError("Refusing symlinked output or receipt")
    if desktop is not None and kind != "rescue":
        raise ValueError("Desktop closure is only supported by RAM rescue")
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    n1_report = json.loads((firmware / "n1-build-report.json").read_text())
    fw_manifest = json.loads((firmware / "firmware-manifest.json").read_text())
    for report in (n1_report, fw_manifest):
        if (report.get("schema"), report.get("target_model"), report.get("board"), report.get("macos_build")) != (1, "Mac17,8", "j716s", FIRMWARE_BUILD):
            raise ValueError("Firmware provenance is not J716s 26C5035r")
    if (n1_report.get("input_sha256") != profile["n1"]["input_sha256"]
            or n1_report.get("manifest_sha256") != profile["n1"]["manifest_sha256"]
            or n1_report.get("output_sha256") != profile["n1"]["output_sha256"]
            or n1_report.get("nonce_free_global_manifest") is not True):
        raise ValueError("N1 report differs from qualified construction pins")
    archive_bytes = archive.read_bytes()
    if sha(archive_bytes) != ALPINE_SHA256:
        raise ValueError("Verified Alpine rescue source differs")
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as source:
        for member in source:
            name = safe_name(member.name)
            if name in ("", ".", "init"):
                continue
            if name in entries:
                raise ValueError("Duplicate archive entry: " + name)
            if member.isdir():
                mode, data = stat.S_IFDIR | member.mode, b""
            elif member.issym():
                mode, data = stat.S_IFLNK | member.mode, member.linkname.encode()
            elif member.isfile() or member.islnk():
                mode, data = stat.S_IFREG | member.mode, source.extractfile(member).read()
            elif member.ischr():
                mode, data = stat.S_IFCHR | member.mode, b""
            elif member.isblk():
                mode, data = stat.S_IFBLK | member.mode, b""
            elif member.isfifo():
                mode, data = stat.S_IFIFO | member.mode, b""
            else:
                raise ValueError("Unsupported archive member: " + name)
            entries[name] = (name, mode, member.uid, member.gid, member.devmajor or 0, member.devminor or 0, data)
    busybox = entries.get("bin/busybox")
    if busybox is None or busybox[6][:4] != b"\x7fELF" or len(busybox[6]) < 20 or struct.unpack_from("<H", busybox[6], 18)[0] != 183:
        raise ValueError("Pinned rescue archive lacks ARM64 BusyBox")
    desktop_sha256 = None
    if desktop is not None:
        desktop_bytes = desktop.read_bytes()
        desktop_receipt = json.loads((desktop.parent / "probe-receipt.json").read_text())
        desktop_sha256 = sha(desktop_bytes)
        if desktop_receipt.get("schema") != 1 or desktop_receipt.get("sha256") != desktop_sha256 or desktop_receipt.get("size") != len(desktop_bytes):
            raise ValueError("RAM desktop closure differs from build receipt")
        entries["desktop"] = ("desktop", stat.S_IFDIR | 0o755, 0, 0, 0, 0, b"")
        with tarfile.open(fileobj=io.BytesIO(desktop_bytes), mode="r:gz") as source:
            for member in source:
                relative = safe_name(member.name)
                if relative in ("", "."):
                    continue
                name = "desktop/" + relative
                if name in entries:
                    raise ValueError("Duplicate desktop archive entry: " + relative)
                if member.isdir():
                    mode, data = stat.S_IFDIR | member.mode, b""
                elif member.issym():
                    mode, data = stat.S_IFLNK | member.mode, member.linkname.encode()
                elif member.isfile() or member.islnk():
                    mode, data = stat.S_IFREG | member.mode, source.extractfile(member).read()
                else:
                    raise ValueError("Desktop closure must not contain device nodes: " + relative)
                entries[name] = (name, mode, member.uid, member.gid, 0, 0, data)
    init_path = HERE / "root-init.sh" if kind == "root" else PROJECT / "outputs/boot-candidate/rescue-init.sh"
    init = init_path.read_bytes()
    entries["init"] = ("init", stat.S_IFREG | 0o755, 0, 0, 0, 0, init)
    for directory in ("dev", "proc", "sys", "run", "newroot", "lib/firmware", "lib/firmware/apple", "lib/firmware/apple/centauri"):
        entries.setdefault(directory, (directory, stat.S_IFDIR | 0o755, 0, 0, 0, 0, b""))
    entries["dev/console"] = ("dev/console", stat.S_IFCHR | 0o600, 0, 0, 5, 1, b"")
    entries["dev/null"] = ("dev/null", stat.S_IFCHR | 0o666, 0, 0, 1, 3, b"")
    files = {"apple/tpmtfw-j716s.bin": firmware / "apple/tpmtfw-j716s.bin",
             "apple/centauri/j716s-ftab.bin": firmware / "apple/centauri/j716s-ftab.bin"}
    firmware_hashes = {}
    for relative, path in files.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or symlinked firmware: " + str(path))
        data = path.read_bytes()
        digest = sha(data)
        if fw_manifest.get("firmware", {}).get(relative) != digest:
            raise ValueError("Firmware differs from build manifest: " + relative)
        expected = profile["n1"]["output_sha256"] if "centauri" in relative else "0d982117cb2d2c1909454ccd331e5a88a6f72e61c0eff3e55c6d884e90a519b3"
        if digest != expected:
            raise ValueError("Firmware differs from qualified output pin: " + relative)
        name = "lib/firmware/" + relative
        entries[name] = (name, stat.S_IFREG | 0o644, 0, 0, 0, 0, data)
        firmware_hashes[relative] = sha(data)
    entries["TRAILER!!!"] = ("TRAILER!!!", 0, 0, 0, 0, 0, b"")
    ordered = sorted((entry for key, entry in entries.items() if key != "TRAILER!!!"),
                     key=lambda entry: (entry[0].count("/"), entry[0])) + [entries["TRAILER!!!"]]
    cpio = newc(ordered)
    compressed = gzip.compress(cpio, compresslevel=6, mtime=0)
    if gzip.decompress(compressed) != cpio:
        raise ValueError("Initramfs compression readback differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb" if replace else "xb") as handle:
        handle.write(compressed)
    receipt = {"schema": 1, "kind": "j716s-persistent-root-initramfs" if kind == "root" else "j716s-rescue-initramfs", "board": "j716s",
               "sha256": sha(compressed), "bytes": len(compressed), "uncompressed_bytes": len(cpio),
               "alpine_source_sha256": ALPINE_SHA256, "init_sha256": sha(init),
               "firmware_build": FIRMWARE_BUILD, "firmware_sha256": firmware_hashes,
               "firmware_manifest_sha256": sha((firmware / "firmware-manifest.json").read_bytes()),
               "n1_build_report_sha256": sha((firmware / "n1-build-report.json").read_bytes()),
               "desktop_closure_sha256": desktop_sha256, "hardware_qualified": False,
               "root_selection": "unique GPT PARTUUID plus expected ext4 filesystem UUID from bootargs" if kind == "root" else "RAM only",
               "root_mount": "read-only, then switch_root; systemd owns fsck/remount/growfs" if kind == "root" else "no block-device mounts",
               "kernel_requirement": "built-in ext4, Apple NVMe, devtmpfs, procfs, sysfs, console and input",
               "status": "workspace artifact; not boot-tested on hardware"}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpine", type=Path, default=PROJECT / "work/j716s-build/rescue/alpine-minirootfs-3.21.3-aarch64.tar.gz")
    parser.add_argument("--firmware", type=Path, default=PROJECT / "outputs/firmware/j716s-26C5035r-linux")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("root", "rescue"), default="root")
    parser.add_argument("--replace-generated", action="store_true", help="replace only the canonical generated boot-candidate archive")
    parser.add_argument("--desktop", type=Path, help="optional secret-free RAM desktop archive and sibling probe-receipt.json")
    args = parser.parse_args()
    build(args.alpine, args.firmware, args.output, args.kind, args.replace_generated, args.desktop)


if __name__ == "__main__":
    main()
