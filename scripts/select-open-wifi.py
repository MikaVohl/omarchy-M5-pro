#!/usr/bin/env python3
"""Check, persist, or roll back the tested J716s open-WiFi boot selection; never reboot."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("open_wifi_stage", PROJECT / "scripts/stage-open-wifi.py")
stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage)
candidate_boot = stage.candidate_boot
BASE = stage.BASE
RELEASE = stage.RELEASE
GUARD = stage.GUARD
HELPER = GUARD.parent / "candidate_boot.py"
RECEIPT = Path("/etc/j716s/open-wifi-default.json")
BACKUPS = Path("/var/lib/j716s/open-wifi-default-backups")
require = stage.require
digest = stage.digest


def fsync_directory(path):
    with stage.open_directory(path) as descriptor:
        os.fsync(descriptor)


def validate_baseline(root, deployment):
    """Check preserved root/rescue bytes, never the candidate or selected payload."""
    root = Path(root)
    for name, key in (("root.bin", "root_payload_sha256"), ("rescue.bin", "rescue_sha256")):
        path = root / "boot/aurora" / name
        stage.check_regular(path)
        require(digest(path) == deployment[key], "preserved baseline differs: " + name)
    fixed = {"aurora/root/Image.gz", "aurora/root/t6050-j716s.dtb",
             "aurora/root/initramfs.cpio.gz", "aurora/rescue/Image.gz",
             "aurora/rescue/initramfs.cpio.gz"}
    entries = deployment["payload_files"]
    paths = {entry["path"] for entry in entries}
    device_trees = paths - fixed
    require(len(entries) == len(paths) == 6 and fixed <= paths
            and len(device_trees) == 1
            and all(Path(path).parent.as_posix() == "aurora/rescue"
                    and Path(path).suffix == ".dtb" for path in device_trees),
            "baseline dependency inventory differs")
    for entry in entries:
        path = root / "boot" / entry["path"]
        stage.check_regular(path)
        require(path.stat().st_size == entry["size"] and digest(path) == entry["sha256"],
                "preserved baseline dependency differs: " + entry["path"])


def atomic_copy(source, destination, expected_sha256):
    """Copy and hash before a same-directory atomic replacement; fsync both sides."""
    source, destination = Path(source), Path(destination)
    stage.check_regular(source)
    require(destination.parent.resolve() == destination.parent and not destination.is_symlink(),
            "unsafe selection destination")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            value = hashlib.sha256()
            with source.open("rb") as original:
                for block in iter(lambda: original.read(1024 * 1024), b""):
                    value.update(block)
                    stream.write(block)
            require(value.hexdigest() == expected_sha256, "payload changed before publication")
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
            pending.replace(destination)
            fsync_directory(destination.parent)
        finally:
            pending.unlink(missing_ok=True)


def rollback_selection(root, deployment):
    """Restore baseline without consulting candidate files, modules, or receipts."""
    root = Path(root)
    validate_baseline(root, deployment)
    selected = root / "boot/aurora/boot.bin"
    atomic_copy(root / "boot/aurora/root.bin", selected, deployment["root_payload_sha256"])
    fsync_directory(root / "boot")
    require(digest(selected) == deployment["root_payload_sha256"], "baseline selection readback differs")


def publish_selection(root, deployment, selected_sha256, after_publish=None):
    """Publish only verified bytes; any publication/readback failure restores root.bin.

    The caller must first authorize candidate files/modules and install the guard
    and receipt. after_publish performs the live full-selection and guard checks.
    This filesystem-only boundary also supports isolated, unprivileged tests.
    """
    root = Path(root)
    validate_baseline(root, deployment)
    try:
        atomic_copy(root / "boot/aurora/open-wifi.bin", root / "boot/aurora/boot.bin", selected_sha256)
        fsync_directory(root / "boot")
        require(digest(root / "boot/aurora/boot.bin") == selected_sha256, "candidate selection readback differs")
        if after_publish is not None:
            after_publish()
    except BaseException as error:
        try:
            rollback_selection(root, deployment)
        except BaseException as rollback_error:
            raise RuntimeError("publication failed (" + str(error) + "); baseline restoration also failed: "
                               + str(rollback_error)) from error
        raise


def trusted_file(path):
    stage.check_regular(path)
    metadata = path.stat()
    require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
            "file must be root-owned and not group/world writable: " + str(path))


def guid(value):
    return str(uuid.UUID(value))


def live_target():
    require(os.geteuid() == 0, "run through sudo or pkexec")
    require(os.uname().machine == "aarch64", "this is not ARM64")
    require(b"apple,j716s" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"), "wrong board")
    require(stage.run("findmnt", "-nro", "TARGET", "--target", "/boot") == "/boot", "ESP must already be mounted at /boot")
    require(stage.run("findmnt", "-nro", "FSTYPE", "--target", "/boot") == "vfat", "ESP is not FAT")
    identity_path = Path("/usr/share/j716s/image-identity.json")
    deployment_path = Path("/boot/aurora/deployment.json")
    trusted_file(identity_path)
    stage.check_regular(deployment_path)
    identity = json.loads(identity_path.read_text())
    deployment = json.loads(deployment_path.read_text())
    require(identity["schema"] == deployment["schema"] == 1 and identity["board"] == deployment["board"] == "j716s",
            "unsupported target identity")
    require(identity["kernel_release"] == deployment["kernel_release"] == BASE, "baseline kernel identity differs")
    require(deployment["image_identity_sha256"] == digest(identity_path), "baseline identity digest differs")
    require(deployment["stage2_path"] == "aurora/boot.bin" and deployment["root_payload_path"] == "aurora/root.bin"
            and deployment["rescue_path"] == "aurora/rescue.bin", "unexpected baseline selection paths")
    cmdline = {}
    for token in Path("/proc/cmdline").read_text().split():
        key, separator, value = token.partition("=")
        if separator:
            require(key not in cmdline or cmdline[key] == value, "conflicting command-line parameter: " + key)
            cmdline[key] = value
    require(cmdline.get("root", "").startswith("PARTUUID="), "root must name its true PARTUUID")
    require(guid(cmdline["root"].removeprefix("PARTUUID=")) == guid(deployment["linux_partition_uuid"])
            and guid(cmdline["j716s.esp-partuuid"]) == guid(deployment["esp_partition_uuid"]),
            "command-line partition binding differs")
    devices = {}
    for row in stage.run("lsblk", "-rpn", "-o", "PATH,PARTUUID").splitlines():
        fields = row.split()
        if len(fields) == 2:
            devices.setdefault(fields[1].lower(), []).append(fields[0])
    mounted = {}
    for target, key, filesystem, partition_type in (
            ("/", "linux_partition_uuid", "ext4", "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("/boot", "esp_partition_uuid", "vfat", "c12a7328-f81f-11d2-ba4b-00a0c93ec93b")):
        matches = devices.get(guid(deployment[key]), [])
        require(len(matches) == 1, "partition PARTUUID is missing or duplicated: " + key)
        device = os.path.realpath(matches[0])
        require(device == os.path.realpath(stage.run("findmnt", "-nro", "SOURCE", "--target", target)),
                "mounted device binding differs: " + target)
        require(stage.run("findmnt", "-nro", "FSTYPE", "--target", target) == filesystem, "filesystem differs: " + target)
        require(stage.run("lsblk", "-dnro", "PARTTYPE", device).lower() == partition_type, "GPT partition type differs")
        mounted[target] = device
    require(guid(deployment["linux_partition_uuid"]) != guid(deployment["esp_partition_uuid"]), "partition identities coincide")
    parent = stage.run("lsblk", "-dnro", "PKNAME", mounted["/"])
    require(parent and parent == stage.run("lsblk", "-dnro", "PKNAME", mounted["/boot"]), "root and ESP are on different disks")
    fsuuid = guid(stage.run("blkid", "-c", "/dev/null", "-s", "UUID", "-o", "value", mounted["/"]))
    require(fsuuid == guid(identity["filesystem_uuid"]) == guid(deployment["filesystem_uuid"])
            == guid(cmdline["j716s.root-fsuuid"]), "root filesystem binding differs")
    validate_baseline(Path("/"), deployment)
    spent_markers = {("spent\naurora/" + variant + ".bin\n").encode("ascii")
                     for variant in candidate_boot.VARIANTS}
    require(stage.marker_location(mounted["/boot"]) in spent_markers,
            "trial marker is armed or belongs to an unreviewed candidate")
    return identity, deployment


def guard_check(path=GUARD, source_helper=False):
    trusted_file(path)
    if not source_helper:
        trusted_file(HELPER)
    environment = dict(os.environ)
    if source_helper:
        environment["PYTHONPATH"] = str(stage.SUPPORT)
    subprocess.run([sys.executable, "-B", str(path), "--check-only"], check=True, env=environment)


def patched_guard(directory):
    trusted_file(GUARD)
    target = Path(directory) / GUARD.name
    shutil.copyfile(GUARD, target)
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "--directory", str(directory),
                    "-i", str(PROJECT / "patches/kernel-open-wifi-persistent-guard.patch")], check=True)
    guard_check(target, source_helper=True)
    return target.read_bytes()


def preserve_runtime():
    """Keep original installed code and its exact hashes in a durable private directory."""
    trusted_file(GUARD)
    trusted_file(HELPER)
    for directory in (BACKUPS.parent, BACKUPS):
        require(directory.parent.resolve() == directory.parent and not directory.is_symlink(), "unsafe backup directory")
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.stat()
        private = directory == BACKUPS
        require(directory.is_dir() and metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0
                and (not private or stat.S_IMODE(metadata.st_mode) == 0o700),
                "unsafe root-owned backup directory: " + str(directory))
        fsync_directory(directory.parent)
    backup = Path(tempfile.mkdtemp(prefix="runtime-", dir=BACKUPS))
    records = {}
    for path in (GUARD, HELPER):
        metadata = path.stat()
        data = path.read_bytes()
        stage.atomic(backup / path.name, data)
        records[str(path)] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                              "mode": stat.S_IMODE(metadata.st_mode), "uid": metadata.st_uid, "gid": metadata.st_gid}
    stage.atomic(backup / "originals.json", (json.dumps(records, sort_keys=True, indent=2) + "\n").encode())
    fsync_directory(backup)
    fsync_directory(BACKUPS)
    return backup


def persistent_receipt(deployment, candidate):
    files = {entry["path"]: entry for entry in candidate["files"]}
    return {"schema": 1, "kind": "j716s-open-wifi-default", "kernel_release": RELEASE,
            "candidate_receipt_sha256": digest(stage.RECEIPT),
            "baseline_payload_sha256": deployment["root_payload_sha256"],
            "selected_payload_sha256": files[stage.TRIAL]["sha256"]}


def select(activate):
    identity, deployment = live_target()
    if activate:
        require(os.uname().release == RELEASE, "activation requires the exact physically tested open-WiFi kernel")
    guard_check()
    candidate = candidate_boot.verify_candidate(Path("/"), BASE, identity, deployment)
    candidate_boot.validate_boot_selection(Path("/"), BASE, identity, deployment)
    receipt = persistent_receipt(deployment, candidate)
    if digest(Path("/boot/aurora/boot.bin")) == receipt["selected_payload_sha256"]:
        require(digest(HELPER) == digest(stage.SUPPORT / "candidate_boot.py"), "installed persistent helper differs")
        print("VERIFIED: tested open-WiFi payload is already the persistent default; no changes or reboot.")
        return
    require(digest(Path("/boot/aurora/boot.bin")) == deployment["root_payload_sha256"], "unexpected current boot selection")
    trusted_file(HELPER)
    with tempfile.TemporaryDirectory(prefix="j716s-persistent-guard-") as temporary:
        guard = patched_guard(temporary)
    if not activate:
        print("CHECK ONLY: tested candidate, actual root/ESP binding, baseline, spent marker and guard upgrade verified; no target changes.")
        return
    backup = preserve_runtime()
    stage.atomic(HELPER, (stage.SUPPORT / "candidate_boot.py").read_bytes(), 0o644)
    stage.atomic(GUARD, guard, 0o755)
    stage.atomic(RECEIPT, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
    trusted_file(RECEIPT)
    require(stat.S_IMODE(RECEIPT.stat().st_mode) == 0o600, "persistent receipt must be mode 0600")
    guard_check()

    def validate_published():
        subprocess.run(["sync", "-f", "/boot"], check=True)
        candidate_boot.validate_boot_selection(Path("/"), BASE, identity, deployment)
        guard_check()

    publish_selection(Path("/"), deployment, receipt["selected_payload_sha256"], validate_published)
    print("ACTIVATED: tested open-WiFi payload is the persistent default. No reboot performed.")
    print("Original guard/helper and hashes preserved privately at " + str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--activate", action="store_true", help="persist the running, receipt-bound tested candidate")
    modes.add_argument("--rollback", action="store_true", help="restore the preserved baseline selection, even if candidate files differ")
    args = parser.parse_args()
    if args.rollback:
        _, deployment = live_target()
        rollback_selection(Path("/"), deployment)
        subprocess.run(["sync", "-f", "/boot"], check=True)
        print("ROLLED BACK: preserved baseline is the default; candidate receipts and upgraded runtime checks retained. No reboot performed.")
    else:
        select(args.activate)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
