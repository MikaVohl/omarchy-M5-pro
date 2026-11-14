#!/usr/bin/env python3
"""Verify or install the fresh J716s root onto its recorded Linux partition.

Default deploy mode is read-only. Explicit --apply writes only the new Linux
partition and the selected ESP; it never partitions, erases macOS, enrolls or
reboots. A partial attempt is not automatically resumed.
"""
import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "outputs/deployment"))
import preflight
from persistent_payload import canonical_uuid, compose, digest, require, root_manifest, rescue_manifest, project_source
from partition_metadata import TYPE_GUIDS, parts

DISKUTIL = "/usr/sbin/diskutil"
RESCUE = PROJECT / "outputs/boot-candidate/j716s-rescue-qualified-kis.bin"


def load_finish():
    path = PROJECT / "outputs/deployment/finish-install-j716s.py"
    spec = importlib.util.spec_from_file_location("j716s_finish", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_image(image, manifest):
    require(image.is_file() and not image.is_symlink(), "Root image missing or symlinked")
    require(image.stat().st_size == manifest["compressed_size"] and digest(image) == manifest["sha256"],
            "Compressed root image differs from manifest")
    result = hashlib.sha256()
    count = 0
    header = b""
    with gzip.open(image, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            if count == 0:
                header = block[:4096]
            count += len(block)
            require(count <= manifest["image_size"], "Root image exceeds declared logical size")
            result.update(block)
    require(count == manifest["image_size"] and result.hexdigest() == manifest["uncompressed_sha256"],
            "Decompressed root image size/hash differs")
    require(len(header) == 4096 and header[1080:1082] == b"\x53\xef", "Image is not ext4")
    require(str(uuid.UUID(bytes=header[1128:1144])) == canonical_uuid(manifest["filesystem_uuid"]), "ext4 UUID differs")
    block_log = struct.unpack_from("<I", header, 1048)[0]
    blocks = struct.unpack_from("<I", header, 1028)[0]
    require(block_log == 2 and blocks * 4096 == count, "ext4 image geometry differs")
    return {"compressed_sha256": manifest["sha256"], "uncompressed_sha256": result.hexdigest(),
            "image_size": count, "filesystem_uuid": canonical_uuid(manifest["filesystem_uuid"])}


def plist(*args):
    return preflight.plist(DISKUTIL, *args)


def partition_layout(entries):
    # Formatting adds volume metadata, not GPT changes. Compare only layout.
    return {guid: (part["Content"], part["Size"]) for guid, part in entries.items()}


def partition_by_guid(whole, guid):
    entries = parts(plist("list", "-plist", whole), whole)
    guid = canonical_uuid(guid).upper()
    require(guid in entries, "Recorded partition GUID is absent")
    part = entries[guid]
    info = plist("info", "-plist", part["DeviceIdentifier"])
    require(canonical_uuid(info.get("DiskUUID")) == canonical_uuid(guid) and info.get("ParentWholeDisk") == whole,
            "Partition info differs from recorded GUID/disk")
    return part, info


def save(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")


def copy_exclusive(source, destination, expected):
    require(not destination.exists() and not destination.is_symlink(), "Refusing existing destination: " + str(destination))
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(digest(destination) == expected, "Copied file readback differs: " + str(destination))


def write_root(image, device, manifest):
    require(stat.S_ISCHR(device.stat().st_mode), "Root target is not a macOS raw device")
    expected_size = manifest["image_size"]
    result = hashlib.sha256()
    count = 0
    with gzip.open(image, "rb") as src, device.open("r+b", buffering=0) as dst:
        while True:
            block = src.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            require(count <= expected_size, "Root stream exceeds verified size")
            result.update(block)
            view = memoryview(block)
            while view:
                written = dst.write(view)
                require(written and written > 0, "Raw root write made no progress")
                view = view[written:]
        os.fsync(dst.fileno())
    require(count == expected_size and result.hexdigest() == manifest["uncompressed_sha256"], "Written root stream differs")
    result = hashlib.sha256()
    with device.open("rb", buffering=0) as src:
        remaining = expected_size
        while remaining:
            block = src.read(min(1024 * 1024, remaining))
            require(bool(block), "Root readback ended early")
            result.update(block)
            remaining -= len(block)
    require(result.hexdigest() == manifest["uncompressed_sha256"], "Raw root readback differs")


def deploy(args):
    finish = load_finish()
    target = finish.target_receipt(args.stub_receipt)
    manifest = root_manifest(args.manifest, args.kernel_dir)
    require(preflight.command("/usr/sbin/sysctl", "-n", "hw.model").decode().strip() == "Mac17,8", "Not J716s")
    require(preflight.command("/usr/bin/sw_vers", "-buildVersion").decode().strip() == preflight.EXPECTED_BUILD,
            "Boot exact staging macOS before persistent deployment")
    require(preflight.boot_firmware() == {"system": preflight.EXPECTED_FIRMWARE, "booted_pairing": preflight.EXPECTED_FIRMWARE},
            "Staging system/paired firmware differs")
    require("Now drawing from 'AC Power'" in preflight.command("/usr/bin/pmset", "-g", "batt").decode(), "Connect AC power")
    root_info = plist("info", "-plist", "/")
    require(canonical_uuid(root_info.get("APFSVolumeGroupID")) == canonical_uuid(target.get("source_volume_group_uuid")),
            "Running macOS is not the recorded staging source group")
    whole, source, container, infos, mounts, group = finish.target_identity(
        target["system_volume_uuid"], target["stub_partition_uuid"], args.stub_receipt)
    finish.stub_assets(argparse.Namespace(stub_receipt=args.stub_receipt, system_uuid=target["system_volume_uuid"],
                                         stub_partuuid=target["stub_partition_uuid"], admin_user=args.admin_user),
                       source, container, infos, mounts, group)
    image_args = argparse.Namespace(receipt=args.prepared / "receipt.json", image=args.prepared / "stage1-boot.bin",
                                    stub_receipt=args.stub_receipt)
    stage1, esp_info, live_rescue = finish.image_receipt(image_args, whole)
    require(stage1["stage2_path"] == "aurora/boot.bin", "Stage 1 must use stable aurora/boot.bin path")
    require(canonical_uuid(target["esp_partition_uuid"]) == canonical_uuid(stage1["esp_partuuid"]), "ESP receipt collision")
    linux_uuid, esp_uuid = canonical_uuid(target["linux_partition_uuid"]), canonical_uuid(target["esp_partition_uuid"])
    source_uuid = canonical_uuid(target["source"]["partition_uuid"])
    isc_uuid = canonical_uuid(target["source"]["isc_partition_uuid"])
    recovery_uuid = canonical_uuid(target["source"]["recovery_partition_uuid"])
    require(len({linux_uuid, esp_uuid, canonical_uuid(target["stub_partition_uuid"]), source_uuid, isc_uuid, recovery_uuid}) == 6,
            "Source/stub/ESP/Linux partition GUIDs collide")
    part, linux_info = partition_by_guid(whole, linux_uuid)
    require(part.get("Content") in ("Linux Filesystem", TYPE_GUIDS["Linux Filesystem"]) and linux_info.get("Content") in
            ("Linux Filesystem", TYPE_GUIDS["Linux Filesystem"]), "Target is not the recorded Linux GPT partition")
    require(abs(part["Size"] - target["proposed_layout"]["linux_bytes"]) <= 2 * 1024**2
            and manifest["image_size"] <= part["Size"], "Linux/image geometry differs")
    require(not linux_info.get("MountPoint") and not linux_info.get("FilesystemType"), "Linux target is mounted or already formatted")
    device = Path("/dev/r" + part["DeviceIdentifier"])
    require(stat.S_ISCHR(device.stat().st_mode), "Linux target does not resolve to a raw disk device")
    with device.open("rb", buffering=0) as handle:
        require(handle.read(4096) == bytes(4096), "Linux target has data; no destructive resume/reinstall")
    rescue_record, rescue_manifest_hash = rescue_manifest(args.kernel_dir, esp_uuid)
    rescue_hash = rescue_record["sha256"]
    initial_boot_hash = stage1["stage2_sha256"]
    require(rescue_hash == initial_boot_hash and digest(live_rescue) == initial_boot_hash,
            "Fresh qualified-recipe rescue or prepared active boot is not intact")
    rescue_files = rescue_record["files"]
    for entry in rescue_files:
        require(digest(finish.contained(finish.mountpoint(esp_info), entry["path"])) == entry["sha256"],
                "Installed rescue dependency differs: " + entry["path"])
    verification = verify_image(args.image, manifest)
    payload, payload_receipt = compose(args.kernel_dir, args.initramfs, manifest, linux_uuid, esp_uuid)
    esp = finish.mountpoint(esp_info)
    destinations = {name: finish.contained(esp, "aurora/" + name, must_exist=False)
                    for name in ("root.bin", "root-manifest.json", "deployment.json", "boot.bin.new", "rescue.bin", "rescue-manifest.json")}
    for name, path in destinations.items():
        if name == "rescue.bin" and path.is_file() and digest(path) == rescue_hash:
            continue
        require(not path.exists(), "Partial or existing deployment path refused: " + str(path))
    for entry in payload_receipt["files"]:
        destination = finish.contained(esp, entry["path"], must_exist=False)
        require(not destination.exists(), "Root boot dependency already exists: " + entry["path"])
    before = parts(plist("list", "-plist", whole), whole)
    expected_guids = {source_uuid.upper(), isc_uuid.upper(), recovery_uuid.upper(),
                      canonical_uuid(target["stub_partition_uuid"]).upper(), linux_uuid.upper(), esp_uuid.upper()}
    require(set(before) == expected_guids, "Fresh layout has unexpected partition GUIDs")
    require(before[isc_uuid.upper()]["Content"] == "Apple_APFS_ISC"
            and before[recovery_uuid.upper()]["Content"] == "Apple_APFS_Recovery",
            "Protected Apple ISC/Recovery partition types changed")
    baseline = target["baseline_partitions"]
    for guid in (isc_uuid, recovery_uuid):
        require(all(before[guid.upper()][key] == baseline[guid.upper()][key] for key in ("Content", "Size")),
                "Protected Apple partition geometry differs from original baseline")
    for guid, content, size_key in ((source_uuid, "Apple_APFS", "macos_container_target_bytes"),
                                    (canonical_uuid(target["stub_partition_uuid"]), "Apple_APFS", "stub_bytes"),
                                    (esp_uuid, "EFI", "esp_bytes")):
        require(before[guid.upper()]["Content"] in (content, TYPE_GUIDS[content])
                and abs(before[guid.upper()]["Size"] - target["proposed_layout"][size_key]) <= 2 * 1024**2,
                "Recorded final GPT partition geometry differs: " + guid)
    protected = {guid: {key: before[guid.upper()][key] for key in ("Content", "Size")}
                 for guid in (source_uuid, isc_uuid, recovery_uuid)}
    receipt = {"schema": 1, "kind": "j716s-omarchy-deployment", "board": "j716s",
               "source_macos_partition_uuid": source_uuid, "isc_partition_uuid": isc_uuid,
               "recovery_partition_uuid": recovery_uuid, "protected_partitions": protected,
               "source_volume_group_uuid": target["source_volume_group_uuid"], "target_receipt_sha256": digest(args.stub_receipt),
               "stub_partition_uuid": target["stub_partition_uuid"], "system_volume_uuid": target["system_volume_uuid"],
               "volume_group_uuid": target["volume_group_uuid"], "linux_partition_uuid": linux_uuid, "esp_partition_uuid": esp_uuid,
               "filesystem_uuid": verification["filesystem_uuid"], "root_manifest_sha256": digest(args.manifest),
               "image_identity_sha256": manifest["image_identity_sha256"], "root_image_sha256": manifest["sha256"],
               "root_image_uncompressed_sha256": manifest["uncompressed_sha256"], "kernel_release": manifest["kernel_release"],
               "firmware_build": manifest["firmware_build"], "stage2_path": "aurora/boot.bin", "root_payload_path": "aurora/root.bin",
               "rescue_path": "aurora/rescue.bin", "root_payload_sha256": payload_receipt["sha256"], "rescue_sha256": rescue_hash,
               "kernel_build_manifest_sha256": manifest["kernel_build_manifest_sha256"],
               "loader_build_manifest_sha256": manifest["loader_build_manifest_sha256"],
               "rescue_manifest_sha256": rescue_manifest_hash,
               "payload_files": [{key: entry[key] for key in ("path", "sha256", "size")}
                                 for entry in payload_receipt["files"] + rescue_files],
               "graphics_profile": "native", "status": "prepared; not written or boot-tested"}
    print(json.dumps(receipt, indent=2))
    if not args.apply:
        print("Read-only deployment check passed. No root/ESP writes, enrollment or reboot.")
        return
    require(os.geteuid() == 0, "--apply requires sudo")
    preflight.confirm_backup()
    journal = Path(tempfile.mkdtemp(prefix="root-attempt.", dir=HERE))
    save(journal / "prepared.json", receipt)
    save(journal / "payload.json", payload_receipt)
    now = finish.target_identity(target["system_volume_uuid"], target["stub_partition_uuid"], args.stub_receipt)
    require(now[0] == whole and now[4] == mounts, "Source/stub identities or mounts changed before apply")
    finish.image_receipt(image_args, whole)
    require(digest(live_rescue) == initial_boot_hash
            and partition_layout(parts(plist("list", "-plist", whole), whole)) == partition_layout(before),
            "Active rescue or GPT layout changed before apply")
    part_now, info_now = partition_by_guid(whole, linux_uuid)
    require(not info_now.get("MountPoint") and not info_now.get("FilesystemType"), "Linux target became mounted or formatted")
    with device.open("rb", buffering=0) as handle:
        require(handle.read(4096) == bytes(4096), "Linux target changed; refusing destructive write")
    require(all(part_now[key] == part[key] for key in ("DeviceIdentifier", "Content", "Size"))
            and finish.mounted_esp(esp_uuid, whole)[1] == esp, "Disk identities/mount changed before apply")
    for entry in payload_receipt["files"]:
        destination = finish.contained(esp, entry["path"], must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_exclusive(project_source(entry["source"]), destination, entry["sha256"])
    if not destinations["rescue.bin"].exists():
        copy_exclusive(RESCUE, destinations["rescue.bin"], rescue_hash)
    copy_exclusive(RESCUE.with_suffix(".bin.json"), destinations["rescue-manifest.json"], rescue_manifest_hash)
    write_root(args.image, device, manifest)
    save(journal / "root-written.json", verification)
    copy_exclusive(args.manifest, destinations["root-manifest.json"], receipt["root_manifest_sha256"])
    with destinations["root.bin"].open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    require(digest(destinations["root.bin"]) == receipt["root_payload_sha256"], "ESP root payload readback differs")
    receipt["status"] = "written; hardware boot pending"
    save(destinations["deployment.json"], receipt)
    # Publish the active boot payload LAST. The original rescue remains intact until replacement.
    copy_exclusive(destinations["root.bin"], destinations["boot.bin.new"], receipt["root_payload_sha256"])
    require(partition_layout(parts(plist("list", "-plist", whole), whole)) == partition_layout(before),
            "GPT metadata changed during root/ESP writes")
    require(digest(live_rescue) == initial_boot_hash, "Prepared active rescue changed before activation")
    os.replace(destinations["boot.bin.new"], live_rescue)
    os.sync() if hasattr(os, "sync") else preflight.command("/bin/sync")
    require(digest(live_rescue) == receipt["root_payload_sha256"] and digest(destinations["rescue.bin"]) == rescue_hash,
            "Active/root or rescue readback differs")
    save(journal / "installed.json", receipt)
    print("Persistent root installed; rescue retained at aurora/rescue.bin. No enrollment/reboot performed.")
    print("Wait at least 60 seconds of disk idle before a separately authorized cold boot.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="operation", required=True)
    verify = modes.add_parser("verify-image", help="offline full compressed/decompressed root hash and ext4 identity")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--image", type=Path, required=True)
    install = modes.add_parser("deploy", help="fresh UUID-pinned root/ESP deployment, read-only by default")
    for name in ("manifest", "image", "stub-receipt", "prepared", "initramfs"):
        install.add_argument("--" + name, type=Path, required=True)
    install.add_argument("--kernel-dir", type=Path, default=PROJECT / "outputs/kernel-candidate")
    install.add_argument("--admin-user", required=True)
    action = install.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="default; no disk or ESP mutations")
    action.add_argument("--apply", action="store_true", help="explicit sudo-only writes to recorded fresh Linux partition/ESP")
    args = parser.parse_args()
    try:
        if args.operation == "verify-image":
            print(json.dumps(verify_image(args.image, root_manifest(args.manifest)), indent=2))
        else:
            deploy(args)
        return 0
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
