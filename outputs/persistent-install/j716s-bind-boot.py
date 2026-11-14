#!/usr/bin/env python3
"""Validate the running J716s root against its UUID-pinned ESP deployment receipt."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

LINUX_TYPE = "0fc63daf-8483-4772-8e79-3d69d8477de4"
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def guid(value):
    return str(uuid.UUID(value))


def atomic(path, value, mode=0o644):
    path = Path(path)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    temporary.replace(path)


def main():
    require(os.geteuid() == 0, "root is required")
    require(os.uname().machine == "aarch64", "this is not ARM64")
    compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    require(b"apple,j716s" in compatible, "the live machine is not J716s")
    cmdline = {}
    for token in Path("/proc/cmdline").read_text().split():
        key, separator, value = token.partition("=")
        if separator:
            require(key not in cmdline or cmdline[key] == value, "conflicting command-line parameter: " + key)
            cmdline[key] = value
    require(cmdline.get("root", "").startswith("PARTUUID="), "root must name its true PARTUUID")
    root_guid = guid(cmdline["root"].removeprefix("PARTUUID="))
    esp_guid = guid(cmdline["j716s.esp-partuuid"])
    require(root_guid != esp_guid, "root and ESP GUIDs coincide")
    devices = {}
    for row in command("lsblk", "-rpn", "-o", "PATH,PARTUUID").splitlines():
        fields = row.split()
        if len(fields) == 2:
            devices.setdefault(fields[1].lower(), []).append(fields[0])
    require(len(devices.get(root_guid, [])) == 1, "root PARTUUID is missing or duplicated")
    require(len(devices.get(esp_guid, [])) == 1, "ESP PARTUUID is missing or duplicated")
    root_device = os.path.realpath(devices[root_guid][0])
    esp_device = os.path.realpath(devices[esp_guid][0])
    mounted_root = os.path.realpath(command("findmnt", "-nro", "SOURCE", "/"))
    require(mounted_root == root_device, "the mounted root is not the command-line partition")
    require(command("findmnt", "-nro", "FSTYPE", "/") == "ext4", "root is not ext4")
    require(command("lsblk", "-dnro", "PARTTYPE", root_device).lower() == LINUX_TYPE, "root is not a Linux filesystem GPT partition")
    require(command("lsblk", "-dnro", "PARTTYPE", esp_device).lower() == ESP_TYPE, "ESP has the wrong GPT type")
    root_parent = command("lsblk", "-dnro", "PKNAME", root_device)
    require(root_parent and root_parent == command("lsblk", "-dnro", "PKNAME", esp_device), "root and ESP are not on the same disk")
    fsuuid = guid(command("blkid", "-c", "/dev/null", "-s", "UUID", "-o", "value", root_device))
    require(command("blkid", "-c", "/dev/null", "-s", "TYPE", "-o", "value", esp_device) == "vfat", "ESP filesystem is not FAT")
    require(guid(cmdline["j716s.root-fsuuid"]) == fsuuid, "command-line image filesystem UUID differs")
    identity_path = Path("/usr/share/j716s/image-identity.json")
    identity = json.loads(identity_path.read_text())
    require(identity["schema"] == 1 and identity["board"] == "j716s", "unsupported embedded image identity")
    require(guid(identity["filesystem_uuid"]) == fsuuid, "root filesystem UUID differs from the embedded image")
    release = Path("/etc/j716s/kernel.release").read_text().strip()
    require(identity["kernel_release"] == release == os.uname().release, "running kernel is not the image kernel")
    require(identity["firmware_build"] == "26C5035r", "unexpected firmware build")
    require(Path("/boot").is_dir() and not Path("/boot").is_symlink(), "unsafe /boot directory")
    mounted = subprocess.run(["mountpoint", "-q", "/boot"]).returncode == 0
    if mounted:
        require(os.path.realpath(command("findmnt", "-nro", "SOURCE", "/boot")) == esp_device, "another device is mounted at /boot")
        require(command("findmnt", "-nro", "FSTYPE", "/boot") == "vfat", "mounted ESP is not FAT")
    else:
        subprocess.run(["mount", "-t", "vfat", "-o", "ro,nosuid,nodev,noexec,umask=0077", esp_device, "/boot"], check=True)
    receipt_path = Path("/boot/aurora/deployment.json")
    manifest_path = Path("/boot/aurora/root-manifest.json")
    receipt = json.loads(receipt_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    require(receipt.get("schema") == 1 and receipt.get("kind") == "j716s-omarchy-deployment", "unsupported deployment receipt")
    require(manifest.get("schema") == 1 and manifest.get("kind") == "j716s-omarchy-rootfs", "unsupported root manifest")
    require(receipt.get("board") == manifest.get("board") == "j716s", "receipt board differs")
    require(guid(receipt["linux_partition_uuid"]) == root_guid and guid(receipt["esp_partition_uuid"]) == esp_guid, "receipt partition binding differs")
    require(guid(receipt["filesystem_uuid"]) == guid(manifest["filesystem_uuid"]) == fsuuid, "receipt filesystem binding differs")
    source_guid = guid(receipt["source_macos_partition_uuid"])
    stub_guid = guid(receipt["stub_partition_uuid"])
    require(len({source_guid, stub_guid, root_guid, esp_guid}) == 4, "source/stub/root/ESP identities are not distinct")
    protected = receipt["protected_partitions"]
    require(set(protected) == {source_guid, guid(receipt["isc_partition_uuid"]), guid(receipt["recovery_partition_uuid"])},
            "protected source partition inventory differs")
    protected_types = {source_guid: "7c3457ef-0000-11aa-aa11-00306543ecac",
                       guid(receipt["isc_partition_uuid"]): "69646961-6700-11aa-aa11-00306543ecac",
                       guid(receipt["recovery_partition_uuid"]): "52637672-7900-11aa-aa11-00306543ecac"}
    require(len(set(protected) | {stub_guid, root_guid, esp_guid}) == 6, "protected/deployment GPT identities collide")
    for partition_guid, expected_type in protected_types.items():
        require(len(devices.get(partition_guid, [])) == 1, "protected partition is missing or duplicated")
        device = devices[partition_guid][0]
        require(command("lsblk", "-dnro", "PARTTYPE", device).lower() == expected_type, "protected GPT type differs")
        require(command("lsblk", "-dnro", "PKNAME", device) == root_parent, "protected partition belongs to another disk")
        require(int(command("blockdev", "--getsize64", device)) == protected[partition_guid]["Size"], "protected partition size differs")
    for partition_guid in (source_guid, stub_guid):
        require(len(devices.get(partition_guid, [])) == 1, "source/stub GPT identity is missing or duplicated")
        device = devices[partition_guid][0]
        require(command("lsblk", "-dnro", "PARTTYPE", device).lower() == "7c3457ef-0000-11aa-aa11-00306543ecac", "source/stub is not an APFS GPT partition")
        require(command("lsblk", "-dnro", "PKNAME", device) == root_parent, "source/stub is not on the deployment disk")
    guid(receipt["system_volume_uuid"])
    guid(receipt["volume_group_uuid"])
    require(manifest["filesystem"] == "ext4" and manifest["block_size"] == 4096, "unexpected root format")
    require(receipt["root_manifest_sha256"] == digest(manifest_path), "external root manifest digest differs")
    require(receipt["image_identity_sha256"] == manifest["image_identity_sha256"] == digest(identity_path), "embedded identity digest differs")
    require(receipt["kernel_release"] == manifest["kernel_release"] == release, "receipt kernel differs")
    require(receipt["firmware_build"] == manifest["firmware_build"] == identity["firmware_build"], "receipt firmware differs")
    for key in ("kernel_build_manifest_sha256", "loader_build_manifest_sha256"):
        require(receipt[key] == manifest[key] == identity[key], "fresh build receipt crossbinding differs: " + key)
    require(identity["kernel_artifacts"] == manifest["kernel_artifacts"], "image kernel inventory differs")
    require(identity.get("graphics_helpers") == manifest.get("graphics_helpers"), "image graphics helper inventory differs")
    for installed, artifact in identity["graphics_helpers"].items():
        require(installed in ("usr/local/bin/j716s-gpu-preflight", "usr/local/libexec/j716s-session-env"), "unexpected graphics helper")
        path = Path("/") / installed
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == artifact["size_bytes"]
                and digest(path) == artifact["sha256"], "installed graphics helper differs: " + installed)
    rescue_path = Path("/boot/aurora/rescue-manifest.json")
    require(digest(rescue_path) == receipt["rescue_manifest_sha256"], "rescue manifest digest differs")
    rescue = json.loads(rescue_path.read_text())
    require(rescue["sha256"] == receipt["rescue_sha256"]
            and guid(rescue["esp_partuuid"]) == esp_guid
            and all(rescue[key] == receipt[key] for key in ("kernel_build_manifest_sha256", "loader_build_manifest_sha256")),
            "rescue/root fresh build crossbinding differs")
    for name, artifact in identity["kernel_artifacts"].items():
        if name != "modules.tar.gz":
            path = Path("/usr/lib/j716s/kernel") / name
            require(path.is_file() and not path.is_symlink() and path.stat().st_size == artifact["size_bytes"]
                    and digest(path) == artifact["sha256"], "installed kernel artifact differs: " + name)
    require(receipt["root_image_sha256"] == manifest["sha256"] and receipt["root_image_uncompressed_sha256"] == manifest["uncompressed_sha256"], "receipt root image digests differ")
    for key, expected, hash_key in (("root_payload_path", "aurora/root.bin", "root_payload_sha256"), ("rescue_path", "aurora/rescue.bin", "rescue_sha256")):
        require(receipt[key] == expected, "unexpected payload path: " + key)
        require(digest(Path("/boot") / expected) == receipt[hash_key], "payload digest differs: " + expected)
    dependencies = receipt.get("payload_files", [])
    require(len(dependencies) == 6 and len({entry["path"] for entry in dependencies}) == 6, "boot dependency inventory differs")
    expected_paths = {"aurora/root/Image.gz", "aurora/root/t6050-j716s.dtb", "aurora/root/initramfs.cpio.gz",
                      "aurora/rescue/Image.gz", "aurora/rescue/t6050-j716s.dtb", "aurora/rescue/initramfs.cpio.gz"}
    require({entry["path"] for entry in dependencies} == expected_paths, "boot dependency paths differ")
    rescue_files = [{key: entry[key] for key in ("path", "sha256", "size")} for entry in rescue["files"]]
    require([entry for entry in dependencies if entry["path"].startswith("aurora/rescue/")] == rescue_files,
            "rescue dependency crossbinding differs")
    for entry in dependencies:
        if entry["path"].endswith("/Image.gz"):
            require(entry["sha256"] == identity["kernel_artifacts"]["Image.gz"]["sha256"], "payload kernel differs from image")
    for entry in dependencies:
        relative = Path(entry["path"])
        require(not relative.is_absolute() and ".." not in relative.parts and relative.parts[:2] in
                (("aurora", "root"), ("aurora", "rescue")), "unsafe boot dependency path")
        path = Path("/boot") / relative
        require(path.resolve() == path and not path.is_symlink() and path.stat().st_size == entry["size"]
                and digest(path) == entry["sha256"], "boot dependency differs: " + str(relative))
    require(receipt["stage2_path"] == "aurora/boot.bin", "unexpected stable stage2 path")
    require(digest("/boot/aurora/boot.bin") == receipt["root_payload_sha256"], "stable stage2 is not the deployed root payload")
    # No GPT writes: only the already-mounted and identity-checked filesystem grows.
    subprocess.run(["mount", "-o", "remount,rw", "/"], check=True)
    subprocess.run(["mount", "-o", "remount,rw,nosuid,nodev,noexec,umask=0077", "/boot"], check=True)
    binding = dict(receipt)
    binding.update(root_device=root_device, esp_device=esp_device, mounted_filesystem_uuid=fsuuid,
                   image_identity_verified=True)
    atomic("/etc/j716s/boot.json", json.dumps(binding, indent=2, sort_keys=True) + "\n", 0o600)
    atomic("/etc/fstab", "# Generated only after J716s deployment identity validation.\n"
           + f"UUID={fsuuid} / ext4 rw,noatime,x-systemd.growfs 0 1\n"
           + f"PARTUUID={esp_guid} /boot vfat nosuid,nodev,noexec,umask=0077 0 2\n")
    target_blocks = int(command("blockdev", "--getsize64", root_device)) // manifest["block_size"]
    subprocess.run(["resize2fs", root_device, str(target_blocks)], check=True)
    print("J716s root/ESP/image/kernel binding verified; ext4 grew without partition changes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print("J716s boot identity validation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
