#!/usr/bin/env python3
"""Check/deploy a pinned persistent kernel variant (bringup, touchid), or restore its previous default.

bringup is the Bluetooth kernel. touchid is bringup plus the J716s sensor wiring
and Touch ID SEP profile; its DTB delta is checked exactly before deployment.

Never reboot, arm a trial, replace a running kernel's modules, or alter baseline
root/rescue dependencies. Deployment is experimental, not physical qualification.
"""
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

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("open_wifi_selection", PROJECT / "scripts/select-open-wifi.py")
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)
stage = selection.stage
candidate = stage.candidate_boot
require = stage.require
HELPER = stage.GUARD.parent / "candidate_boot.py"
DEPLOYABLE = {"bringup": (stage.BASE, stage.RELEASE), "touchid": (stage.BASE, stage.RELEASE, candidate.BRINGUP_RELEASE)}
# Defaults a rollback may restore besides the baseline payload.
PREVIOUS_VARIANTS = ("open-wifi", "bringup")


def select(variant):
    global VARIANT, RELEASE, PREFIX, RECEIPT, DEFAULT, BACKUP_RECEIPT, BACKUPS
    require(variant in DEPLOYABLE, "unsupported persistent variant")
    VARIANT = variant
    RELEASE = candidate.VARIANTS[variant]["release"]
    PREFIX = "aurora/" + variant
    RECEIPT = Path("/etc/j716s/" + variant + ".json")
    DEFAULT = Path("/etc/j716s/" + variant + "-default.json")
    BACKUP_RECEIPT = Path("/etc/j716s/" + variant + "-backup.json")
    BACKUPS = Path("/var/lib/j716s/" + variant + "-backups")


select("bringup")


def private_backups():
    for directory in (BACKUPS.parent, BACKUPS):
        require(not directory.is_symlink() and directory.parent.resolve() == directory.parent,
                "unsafe backup directory")
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.stat()
        require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
                "backup directory must be root-owned and not writable by others")
    require(stat.S_IMODE(BACKUPS.stat().st_mode) == 0o700, "backup directory must be private")


def preserve_previous(deployment):
    private_backups()
    backup = Path(tempfile.mkdtemp(prefix="selection-", dir=BACKUPS))
    records = {}
    for name, source in (("previous-boot.bin", Path("/boot/aurora/boot.bin")),
                         ("candidate_boot.py", HELPER), ("j716s-bind-boot.py", stage.GUARD)):
        selection.trusted_file(source)
        data = source.read_bytes()
        stage.atomic(backup / name, data)
        records[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    record = {"schema": 1, "directory": str(backup), "files": records,
              "baseline_deployment_sha256": stage.digest("/boot/aurora/deployment.json"),
              "baseline_payload_sha256": deployment["root_payload_sha256"]}
    stage.atomic(backup / "originals.json", (json.dumps(record, indent=2) + "\n").encode())
    stage.atomic(BACKUP_RECEIPT, (json.dumps(record, indent=2) + "\n").encode())
    selection.fsync_directory(backup)
    selection.fsync_directory(BACKUPS)
    return backup, record


def previous_backup(deployment):
    selection.trusted_file(BACKUP_RECEIPT)
    record = json.loads(BACKUP_RECEIPT.read_text())
    require(record["schema"] == 1 and record["baseline_deployment_sha256"] == stage.digest("/boot/aurora/deployment.json")
            and record["baseline_payload_sha256"] == deployment["root_payload_sha256"], "backup baseline differs")
    backup = Path(record["directory"])
    require(backup.parent == BACKUPS and backup.resolve() == backup and not backup.is_symlink(), "unsafe backup path")
    metadata = backup.stat()
    require(metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o700, "backup must be root-private")
    previous = backup / "previous-boot.bin"
    selection.trusted_file(previous)
    require(stage.record(previous) == record["files"]["previous-boot.bin"], "previous payload backup differs")
    return previous, record


def publish(source, destination, previous, previous_sha256, expected_sha256, validate):
    """Publish last; any failure restores the previous default, not a new fallback."""
    try:
        selection.atomic_copy(source, destination, expected_sha256)
        subprocess.run(["sync", "-f", str(destination.parent)], check=True)
        validate()
    except BaseException:
        selection.atomic_copy(previous, destination, previous_sha256)
        subprocess.run(["sync", "-f", str(destination.parent)], check=True)
        raise


def rollback():
    identity, deployment = selection.live_target()
    previous, record = previous_backup(deployment)
    expected = record["files"]["previous-boot.bin"]["sha256"]
    if expected != deployment["root_payload_sha256"]:
        for variant in PREVIOUS_VARIANTS:
            if not Path("/etc/j716s/" + variant + "-default.json").exists():
                continue
            old = candidate.verify_candidate(Path("/"), stage.BASE, identity, deployment, variant)
            if next(entry["sha256"] for entry in old["files"] if entry["path"] == "aurora/" + variant + ".bin") == expected:
                candidate._validate_persistent_selection(Path("/"), deployment, old, expected)
                break
        else:
            raise RuntimeError("previous default is neither the baseline nor a preserved pinned default")
    selection.atomic_copy(previous, Path("/boot/aurora/boot.bin"), expected)
    subprocess.run(["sync", "-f", "/boot"], check=True)
    candidate.validate_boot_selection(Path("/"), stage.BASE, identity, deployment)
    print("ROLLED BACK: previous authorized default restored; baseline/rescue and running kernel unchanged. No reboot.")


def deploy(activate, baseline_config):
    identity, deployment = selection.live_target()
    selection.guard_check()
    require(os.uname().release in DEPLOYABLE[VARIANT], "deploy " + VARIANT + " only from " + ", ".join(DEPLOYABLE[VARIANT]))
    candidate.validate_boot_selection(Path("/"), stage.BASE, identity, deployment)
    for path in (RECEIPT, DEFAULT, BACKUP_RECEIPT, Path("/boot") / (PREFIX + ".bin"),
                 Path("/boot") / PREFIX, Path("/usr/lib/modules") / RELEASE):
        require(not path.exists() and not path.is_symlink(), VARIANT + " already staged; inspect/rollback instead of overwriting: " + str(path))
    with tempfile.TemporaryDirectory(prefix="j716s-" + VARIANT + "-") as temporary:
        files, receipt, modules = stage.build_candidate(temporary, identity, deployment,
                                                       variant=VARIANT, baseline_config=baseline_config)
        if VARIANT == "touchid":
            # Exactly the sensor trial's DT delta (pinned SPI2/GPIO wiring, path-matched renumbering).
            spec = importlib.util.spec_from_file_location("j716s_sep_stage", PROJECT / "scripts/stage-sep-probe.py")
            sep_stage = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sep_stage)
            sep_stage.select("sep-sensor")
            sep_stage.validate_dtb_delta(next(data for name, data in files if name == PREFIX + "/t6050-j716s.dtb"))
        needed = sum(len(data) for _, data in files) + 8 * 1024**2
        require(shutil.disk_usage("/boot").free >= needed, "insufficient ESP space; no retained files will be deleted")
        # Exercise the existing installed guard with the new helper before changing it.
        preview = Path(temporary) / stage.GUARD.name
        shutil.copyfile(stage.GUARD, preview)
        shutil.copyfile(stage.SUPPORT / "candidate_boot.py", Path(temporary) / "candidate_boot.py")
        selection.guard_check(preview)
        print("Verified pinned build, Bluetooth-only config delta, preserved baseline/rescue, partition binding and ESP space.")
        if not activate:
            print("CHECK ONLY: candidate composed; no target files changed.")
            return
        backup, originals = preserve_previous(deployment)
        shutil.copytree(modules, Path("/usr/lib/modules") / RELEASE)
        (Path("/boot") / PREFIX).mkdir()
        for name, data in files:
            stage.atomic(Path("/boot") / name, data)
        stage.atomic(RECEIPT, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
        stage.atomic(HELPER, (stage.SUPPORT / "candidate_boot.py").read_bytes(), 0o644)
        candidate.verify_candidate(Path("/"), stage.BASE, identity, deployment, VARIANT)
        selection.guard_check()
        selected = next(entry["sha256"] for entry in receipt["files"] if entry["path"] == PREFIX + ".bin")
        persistent = {"schema": 1, "kind": "j716s-" + VARIANT + "-default", "kernel_release": RELEASE,
                      "candidate_receipt_sha256": stage.digest(RECEIPT),
                      "baseline_payload_sha256": deployment["root_payload_sha256"],
                      "selected_payload_sha256": selected}
        stage.atomic(DEFAULT, (json.dumps(persistent, sort_keys=True, indent=2) + "\n").encode())
        subprocess.run(["sync", "-f", "/boot"], check=True)
        subprocess.run(["sync", "-f", str(Path("/usr/lib/modules") / RELEASE)], check=True)

        def validate():
            candidate.validate_boot_selection(Path("/"), stage.BASE, identity, deployment)
            selection.guard_check()

        publish(Path("/boot") / (PREFIX + ".bin"), Path("/boot/aurora/boot.bin"),
                backup / "previous-boot.bin", originals["files"]["previous-boot.bin"]["sha256"], selected, validate)
        print("DEPLOYED: experimental " + VARIANT + " candidate is the next persistent default; no reboot or trial arming.")
        print("Previous default and runtime bytes preserved at " + str(backup))
        print("Physical boot, peripherals and recovery with this kernel remain unqualified until observed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--activate", action="store_true", help="deploy verified experimental candidate as persistent default")
    modes.add_argument("--rollback", action="store_true", help="restore the previous authorized default")
    parser.add_argument("--variant", choices=sorted(DEPLOYABLE), default="bringup")
    parser.add_argument("--acknowledge-unbooted-risk", action="store_true", help="authorize default activation before physical qualification")
    parser.add_argument("--baseline-config", type=Path, default=PROJECT / "outputs/kernel-open-wifi-candidate/.config",
                        help="baseline .config; must match the installed image identity")
    args = parser.parse_args()
    select(args.variant)
    require(not args.activate or args.acknowledge_unbooted_risk, "activation requires --acknowledge-unbooted-risk")
    if args.rollback:
        rollback()
    else:
        deploy(args.activate, args.baseline_config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
