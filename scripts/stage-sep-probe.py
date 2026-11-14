#!/usr/bin/env python3
"""Stage/check or explicitly arm/cancel a discovery-only J716s SEP trial; never reboot."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
SUPPORT = PROJECT / "outputs/persistent-install"
sys.path.insert(0, str(SUPPORT))
import candidate_boot

spec = importlib.util.spec_from_file_location("j716s_trial_stage", PROJECT / "scripts/stage-open-wifi.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
spec = importlib.util.spec_from_file_location("j716s_payload", PROJECT / "work/aura-audit/compose-linux-rescue.py")
payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)

VARIANT = "sep-probe"
PREFIX = "aurora/sep-probe"
RECEIPT = Path("/etc/j716s/sep-probe.json")
GUARD = Path("/usr/local/lib/j716s/j716s-bind-boot.py")
HELPER = GUARD.parent / "candidate_boot.py"
ESP = Path("/boot")
WORK = PROJECT / "work/build/sep-probe-stage"
ARMING_RECORD = Path("/etc/j716s/sep-probe-arming.json")
ARMED_MARKER = ("armed\n" + PREFIX + ".bin\n").encode("ascii")
SPENT_MARKER = ("spent\n" + PREFIX + ".bin\n").encode("ascii")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def protected_snapshot():
    paths = [ESP / ("aurora/" + name) for name in
             ("boot.bin", "root.bin", "rescue.bin", "deployment.json", "root-manifest.json")]
    paths += [ESP / "aura-try", GUARD, Path("/usr/share/j716s/image-identity.json")]
    deployment = json.loads((ESP / "aurora/deployment.json").read_text())
    paths += [ESP / item["path"] for item in deployment["payload_files"]]
    for path in paths:
        trial.check_regular(path)
    return {path: trial.record(path) for path in paths}


def preflight(allow_armed=False):
    require(os.geteuid() == 0, "run through sudo")
    require(os.uname().machine == "aarch64", "this is not ARM64")
    require(b"apple,j716s" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"), "wrong board")
    require(os.uname().release in (candidate_boot.BASELINE_RELEASE, candidate_boot.CANDIDATE_RELEASE,
                                  candidate_boot.BRINGUP_RELEASE),
            "prepare from the authorized baseline, open-WiFi or bringup kernel")
    require(subprocess.check_output(["findmnt", "-nro", "FSTYPE", "--target", str(ESP)], text=True).strip() == "vfat",
            "ESP is not mounted as FAT")
    subprocess.run([sys.executable, str(GUARD), "--check-only"], check=True)
    device = subprocess.check_output(["findmnt", "-nro", "SOURCE", "--target", str(ESP)], text=True).strip()
    marker = trial.marker_location(device)
    require(marker.startswith(b"spent\n") or (allow_armed and marker == ARMED_MARKER),
            "another trial is armed; leaving it untouched")
    identity = json.loads(Path("/usr/share/j716s/image-identity.json").read_text())
    deployment = json.loads((ESP / "aurora/deployment.json").read_text())
    require(identity["kernel_release"] == deployment["kernel_release"] == candidate_boot.BASELINE_RELEASE,
            "baseline image identity differs")
    return identity, deployment, device


def validate_dtb_delta(candidate):
    """Preserve the actual qualified tree, including its KIS safety disables."""
    before = payload.dtb_nodes((ESP / "aurora/root/t6050-j716s.dtb").read_bytes())
    after = payload.dtb_nodes(candidate)
    require(before.keys() == after.keys(), "SEP discovery candidate changed DT topology")
    require(after["/"]["compatible"].split(b"\0") == before["/"]["compatible"].split(b"\0"), "candidate changed board")
    changes = []
    for node, properties in before.items():
        expected = dict(properties)
        if node == "/aliases":
            require("sep" not in expected, "baseline already has a SEP alias")
            expected["sep"] = b"/soc@200000000/sep@502600000\0"
        elif node == "/soc@200000000/mailbox@502608000":
            expected["status"] = b"okay\0"
        elif properties.get("label") == b"sep\0" and node.endswith("/power-controller@298"):
            expected["status"] = b"okay\0"
            expected["apple,always-on"] = b""
        require(after[node] == expected, "unrelated or unsafe DT change: " + node)
        if expected != properties:
            changes.append(node)
    require(len(changes) == 3, "discovery-only DT delta is incomplete")
    require(after["/soc@200000000/sep@502600000"]["status"] == b"disabled\0", "SEP must await explicit module loading")
    require(after["/soc/spi@505108000"]["status"] == b"disabled\0", "sensor SPI must stay disabled")
    print("Verified exact DT delta: SEP alias, mailbox, always-on SEP power supplier; sensor and qualified KIS topology unchanged.")


def check_staged():
    identity, deployment, _ = preflight()
    receipt = candidate_boot.verify_candidate(Path("/"), candidate_boot.BASELINE_RELEASE, identity, deployment, VARIANT)
    validate_dtb_delta((ESP / (PREFIX + "/t6050-j716s.dtb")).read_bytes())
    require(trial.digest(HELPER) == trial.digest(SUPPORT / "candidate_boot.py"), "installed trial validator differs")
    print("STAGED AND UNARMED: " + receipt["kernel_release"])
    print("No reboot, enrollment, enclave-storage write, or default boot change performed.")


def write_marker_checked(marker, expected, data, readback, sync, after_write=None):
    """Keep the FAT allocation in place; disk/validation failure restores old bytes."""
    trial.check_regular(marker)
    require(8 <= len(expected) <= 512 and 8 <= len(data) <= 512, "invalid marker size")
    require(marker.read_bytes() == expected, "trial marker changed before update")

    def write(value):
        with marker.open("r+b") as stream:
            stream.write(value)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        sync()

    try:
        write(data)
        require(readback() == data, "trial marker direct-disk readback differs")
        if after_write is not None:
            after_write()
    except BaseException:
        write(expected)
        require(readback() == expected, "original marker restoration readback differs")
        raise


def arm(backup_status):
    identity, deployment, device = preflight()
    receipt = candidate_boot.verify_candidate(Path("/"), candidate_boot.BASELINE_RELEASE,
                                              identity, deployment, VARIANT)
    validate_dtb_delta((ESP / (PREFIX + "/t6050-j716s.dtb")).read_bytes())
    require(trial.digest(HELPER) == trial.digest(SUPPORT / "candidate_boot.py"), "installed validator differs")
    require(not ARMING_RECORD.exists() and not ARMING_RECORD.is_symlink(),
            "earlier SEP arming record exists; inspect it before another experiment")
    snapshot = protected_snapshot()
    marker = ESP / "aura-try"
    old = trial.marker_location(device)
    record = {"schema": 1, "kind": "j716s-sep-discovery-arming",
              "trial_id": receipt["trial_id"], "kernel_release": receipt["kernel_release"],
              "candidate_receipt_sha256": trial.digest(RECEIPT),
              "baseline_deployment_sha256": trial.digest(ESP / "aurora/deployment.json"),
              "preserved_default_sha256": trial.digest(ESP / "aurora/boot.bin"),
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "backup_status": backup_status, "attended_cold_boot": True,
              "previous_marker": old.decode("ascii"), "requested_marker": ARMED_MARKER.decode("ascii"),
              "enrollment_authorized": False, "enclave_writes_authorized": False}
    trial.atomic(ARMING_RECORD, (json.dumps(record, indent=2) + "\n").encode())

    def unchanged_selection():
        for path, expected in snapshot.items():
            if path != marker:
                require(trial.record(path) == expected, "protected state changed during arming: " + str(path))

    write_marker_checked(marker, old, ARMED_MARKER, lambda: trial.marker_location(device),
                         lambda: subprocess.run(["sync", "-f", str(ESP)], check=True), unchanged_selection)
    print("ARMED ONCE: the next Linux start selects " + receipt["kernel_release"])
    print("Persistent bringup default, baseline/rescue, guard and dependencies unchanged.")
    print("Backup status: " + backup_status + "; non-writing discovery only, no sensor/enrollment.")
    print("No reboot performed. Shut down fully, then start Linux with physical recovery available.")


def cancel():
    _, _, device = preflight(allow_armed=True)
    marker = ESP / "aura-try"
    old = trial.marker_location(device)
    if old.startswith(b"spent\n"):
        print("No trial is armed; no files changed.")
        return
    require(old == ARMED_MARKER, "trial marker belongs to another candidate")
    write_marker_checked(marker, old, SPENT_MARKER, lambda: trial.marker_location(device),
                         lambda: subprocess.run(["sync", "-f", str(ESP)], check=True))
    print("CANCELLED: SEP trial disarmed; persistent default unchanged, no reboot.")


def stage(apply):
    identity, deployment, device = preflight()
    snapshot = protected_snapshot()
    release = candidate_boot.VARIANTS[VARIANT]["release"]
    destination = Path("/usr/lib/modules") / release
    for path in (RECEIPT, ESP / PREFIX, ESP / (PREFIX + ".bin"), destination):
        require(not path.exists() and not path.is_symlink(), "candidate path already exists: " + str(path))
    trial.check_regular(HELPER)
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = WORK.lstat()
    require(not WORK.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o077, "unsafe staging workspace")
    with tempfile.TemporaryDirectory(dir=WORK) as temporary:
        files, receipt, modules = trial.build_candidate(temporary, identity, deployment, VARIANT)
        dtb = next(data for name, data in files if name == PREFIX + "/t6050-j716s.dtb")
        validate_dtb_delta(dtb)
        require(shutil.disk_usage(ESP).free >= sum(len(data) for _, data in files) + 8 * 1024**2,
                "insufficient ESP space; existing files will not be deleted")
        helper_before = HELPER.read_bytes()
        helper_after = (SUPPORT / "candidate_boot.py").read_bytes()
        if not apply:
            print("CHECK ONLY: candidate payload/modules verified; no installed files changed.")
            return
        backup = WORK / "candidate_boot-before.py"
        require(not backup.exists(), "existing validator backup; inspect the earlier attempt")
        trial.atomic(backup, helper_before)
        created_modules = created_esp = helper_changed = False
        try:
            created_modules = True
            shutil.copytree(modules, destination)
            (ESP / PREFIX).mkdir()
            created_esp = True
            for name, data in files:
                trial.atomic(ESP / name, data)
            trial.atomic(RECEIPT, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
            helper_changed = True
            trial.atomic(HELPER, helper_after, 0o644)
            candidate_boot.verify_candidate(Path("/"), candidate_boot.BASELINE_RELEASE, identity, deployment, VARIANT)
            subprocess.run([sys.executable, str(GUARD), "--check-only"], check=True)
            for path, expected in snapshot.items():
                require(trial.record(path) == expected, "protected state changed during staging: " + str(path))
            require(trial.marker_location(device).startswith(b"spent\n"), "trial marker changed during staging")
            subprocess.run(["sync", "-f", str(ESP)], check=True)
            subprocess.run(["sync", "-f", str(destination)], check=True)
        except BaseException:
            if helper_changed:
                trial.atomic(HELPER, helper_before, 0o644)
            RECEIPT.unlink(missing_ok=True)
            (ESP / (PREFIX + ".bin")).unlink(missing_ok=True)
            if created_esp:
                shutil.rmtree(ESP / PREFIX)
            if created_modules:
                shutil.rmtree(destination)
            raise
    print("STAGED AND UNARMED: " + release)
    print("Default boot payload, rescue, boot dependencies, guard and spent trial marker preserved.")
    print("No reboot performed. SEP loading requires the later attended discovery experiment.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stage", action="store_true", help="install the isolated candidate, without arming")
    modes.add_argument("--check-staged", action="store_true", help="validate the already staged, unarmed candidate")
    modes.add_argument("--arm", action="store_true", help="arm the verified SEP candidate once for an attended cold boot")
    modes.add_argument("--cancel", action="store_true", help="disarm only this SEP trial without requiring candidate modules")
    parser.add_argument("--acknowledge-risk", action="store_true", help="acknowledge experimental SEP/boot recovery risks")
    parser.add_argument("--attended-cold-boot", action="store_true", help="attest saved work and physical recovery access")
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument("--backup-verified", action="store_true", help="attest a current verified backup and retained macOS")
    backup.add_argument("--waive-backup", action="store_true", help="explicitly accept proceeding without the recommended backup")
    args = parser.parse_args()
    require(not (args.stage or args.arm) or args.acknowledge_risk, "staging/arming requires --acknowledge-risk")
    require(not args.arm or (args.attended_cold_boot and (args.backup_verified or args.waive_backup)),
            "arming requires attended cold boot and explicit backup attestation or waiver")
    if args.arm:
        arm("owner-attested-verified" if args.backup_verified else "waived-by-owner")
    elif args.cancel:
        cancel()
    elif args.check_staged:
        check_staged()
    else:
        stage(args.stage)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
