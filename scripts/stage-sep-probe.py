#!/usr/bin/env python3
"""Stage/check or explicitly arm/cancel a one-shot J716s SEP trial; never reboot.

sep-probe is discovery-only. sep-sensor adds one bounded, non-enrolling sensor
identity/authentication probe and installs an anchored same-unit factory package.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import struct
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

GUARD = Path("/usr/local/lib/j716s/j716s-bind-boot.py")
HELPER = GUARD.parent / "candidate_boot.py"
ESP = Path("/boot")
SEP_NODE = "/soc@200000000/sep@502600000"
MAILBOX = "/soc@200000000/mailbox@502608000"
SENSOR_SPI = "/soc/spi@505108000"
SENSOR_NODE = SENSOR_SPI + "/fingerprint@0"
NUB_GPIO = "/soc/pinctrl@2881e4000"
AP_GPIO = "/soc@200000000/pinctrl@29c000000"
SENSOR_POWER = {"/soc@200000000/power-management@280900000/power-controller@330": b"spi2\0",
                "/soc@200000000/power-management@288300000/power-controller@88": b"nub-gpio\0"}
# Cell layout of phandle-bearing properties seen in this tree: (stride, phandle position).
PHANDLE_CELLS = {"phandle": (1, 0), "interrupt-parent": (1, 0), "power-domains": (1, 0),
                 "remote-endpoint": (1, 0), "iommus": (2, 0), "gpio-ranges": (4, 0)}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def select(variant):
    global VARIANT, PREFIX, RECEIPT, WORK, ARMING_RECORD, ARMED_MARKER, SPENT_MARKER
    require(variant in candidate_boot.ONE_SHOT_PARAMETERS, "unsupported SEP trial variant")
    VARIANT = variant
    PREFIX = "aurora/" + variant
    RECEIPT = Path("/etc/j716s/" + variant + ".json")
    WORK = PROJECT / "work/build" / (variant + "-stage")
    ARMING_RECORD = Path("/etc/j716s/" + variant + "-arming.json")
    ARMED_MARKER = ("armed\n" + PREFIX + ".bin\n").encode("ascii")
    SPENT_MARKER = ("spent\n" + PREFIX + ".bin\n").encode("ascii")


select("sep-probe")


def cells(*values):
    return struct.pack(">%dI" % len(values), *values)


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
    allowed = {candidate_boot.BASELINE_RELEASE, candidate_boot.CANDIDATE_RELEASE, candidate_boot.BRINGUP_RELEASE}
    if VARIANT == "sep-sensor":
        # Factory material checks need the local policy that only SEP-alias boots forward.
        allowed.add(candidate_boot.SEP_PROBE_RELEASE)
    require(os.uname().release in allowed, "prepare from an authorized baseline, open-WiFi, bringup or (sensor) discovery kernel")
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


def renumbered(before, after):
    """Baseline nodes rewritten into the candidate's phandle numbering, matched by node path."""
    owners = {}
    for node, properties in before.items():
        if "phandle" in properties:
            require(properties["phandle"] not in owners, "duplicate baseline phandle")
            owners[properties["phandle"]] = node

    def translate(word):
        node = owners.get(word)
        if node is None:
            return word
        require("phandle" in after.get(node, {}), "candidate dropped a referenced phandle: " + node)
        return after[node]["phandle"]

    expected = {}
    for node, properties in before.items():
        expected[node] = {}
        for name, value in properties.items():
            if name in PHANDLE_CELLS:
                stride, position = PHANDLE_CELLS[name]
                require(len(value) % (4 * stride) == 0, "malformed phandle property: " + node + " " + name)
                words = [value[i:i + 4] for i in range(0, len(value), 4)]
                value = b"".join(translate(word) if index % stride == position else word
                                 for index, word in enumerate(words))
            expected[node][name] = value
    return expected


def sensor_delta(before, after, expected):
    """Exactly the J716s sensor wiring from kernel-j716s-sep-sensor.patch."""
    for node, label in SENSOR_POWER.items():
        require(before[node].get("label") == label, "unexpected sensor power domain: " + node)
        expected[node]["status"] = b"okay\0"
    nub = before[NUB_GPIO]["phandle"]
    for node, properties in before.items():
        extended = properties.get("interrupts-extended", b"")
        require(properties.get("interrupt-parent") != nub and nub not in
                [extended[i:i + 4] for i in range(0, len(extended), 4)],
                "baseline has a nub GPIO interrupt consumer: " + node)
    for name in ("interrupt-controller", "#interrupt-cells", "interrupt-parent", "interrupts"):
        require(name in expected[NUB_GPIO], "baseline nub GPIO lacks " + name)
        del expected[NUB_GPIO][name]
    expected[NUB_GPIO].update({"status": b"okay\0", "gpio-reserved-ranges": cells(0, 21, 22, 39)})
    require(before[AP_GPIO].get("gpio-reserved-ranges") == cells(0, 80, 81, 25, 108, 26, 135, 4, 140, 81),
            "baseline AP GPIO reservations differ")
    expected[AP_GPIO]["gpio-reserved-ranges"] = cells(0, 80, 81, 25, 108, 26, 135, 3, 140, 81)
    expected[SENSOR_SPI].update(status=b"okay\0", clocks=after["/clock-ref"]["phandle"])
    require(SENSOR_NODE not in before, "baseline already describes the sensor")
    expected[SENSOR_NODE] = {
        "compatible": b"apple,mesa-fingerprint\0", "reg": cells(0), "spi-max-frequency": cells(8000000),
        "spi-cpol": b"", "spi-cs-setup-delay-ns": cells(20), "spi-cs-hold-delay-ns": cells(20),
        "enable-gpios": after[NUB_GPIO]["phandle"] + cells(21, 0),
        "interrupt-parent": after[AP_GPIO]["phandle"], "interrupts": cells(138, 1),
        "firmware-name": b"apple/mesacal-j716s.bin\0apple/msrk-j716s.bin\0"}


def validate_dtb_delta(candidate, ane_type_metadata=False):
    """Preserve the actual qualified tree, including its KIS safety disables."""
    before = payload.dtb_nodes((ESP / "aurora/root/t6050-j716s.dtb").read_bytes())
    after = payload.dtb_nodes(candidate)
    require(after["/"]["compatible"].split(b"\0") == before["/"]["compatible"].split(b"\0"), "candidate changed board")
    require("sep" not in before["/aliases"], "baseline already has a SEP alias")
    expected = renumbered(before, after)
    expected["/aliases"]["sep"] = (SEP_NODE + "\0").encode()
    expected[MAILBOX]["status"] = b"okay\0"
    power = [node for node, properties in before.items()
             if properties.get("label") == b"sep\0" and node.endswith("/power-controller@298")]
    require(len(power) == 1, "baseline SEP power domain is missing or ambiguous")
    expected[power[0]].update({"status": b"okay\0", "apple,always-on": b""})
    if VARIANT == "sep-sensor":
        sensor_delta(before, after, expected)
    else:
        require(all(after[node].get("phandle") == properties.get("phandle") for node, properties in before.items()
                    if node in after), "discovery candidate renumbered phandles")
    if ane_type_metadata:
        ane = "/soc/neural-processor@508000000"
        require(VARIANT == "sep-sensor" and ane in before and "apple,ane-type" not in before[ane],
                "unexpected baseline for exact ANE metadata correction")
        expected[ane]["apple,ane-type"] = cells(0x220)
    differing = sorted(node for node in expected.keys() | after.keys() if expected.get(node) != after.get(node))
    require(not differing, "unrelated or unsafe DT change: " + ", ".join(differing))
    require(after[SEP_NODE]["status"] == b"disabled\0", "SEP must await explicit module loading")
    if VARIANT == "sep-sensor":
        metadata = " plus exact native ANE type" if ane_type_metadata else ""
        print("Verified exact DT delta: SEP alias/mailbox/power plus the pinned sensor SPI/GPIO wiring" + metadata +
              "; remaining changes are path-matched phandle renumbering; qualified KIS topology unchanged.")
    else:
        require(after[SENSOR_SPI]["status"] == b"disabled\0", "sensor SPI must stay disabled")
        print("Verified exact DT delta: SEP alias, mailbox, always-on SEP power supplier; sensor and qualified KIS topology unchanged.")


def sensor_material(package, provenance_sha256):
    """Checked same-unit factory package as (absolute path, bytes, mode) plus its receipt binding."""
    spec = importlib.util.spec_from_file_location("j716s_sensor_firmware", PROJECT / "scripts/prepare-sensor-firmware.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    try:
        outputs, _ = tool.checked_package(package, provenance_sha256)
    except tool.Refused as error:
        raise RuntimeError("sensor package refused: " + str(error)) from None
    report = (Path(package) / tool.REPORT).read_bytes()
    require(trial.sha(report) == provenance_sha256, "sensor provenance changed after checking")
    require(set(outputs) == candidate_boot.SENSOR_FIRMWARE_NAMES, "sensor package inventory differs")
    private = Path("/") / candidate_boot.SENSOR_MATERIAL_PREFIX
    material = [(Path("/") / candidate_boot.SENSOR_PROVENANCE, report, 0o600)]
    for name, data in sorted(outputs.items()):
        material += [(private / name, data, 0o600), (Path("/usr/lib/firmware") / name, data, 0o644)]
    serial = Path("/sys/firmware/devicetree/base/serial-number").read_bytes().rstrip(b"\0")
    binding = {"provenance_sha256": provenance_sha256, "unit_serial_sha256": trial.sha(serial),
               "current_restore_build_verified": False,
               "files": [{"path": str(path.relative_to("/")), "size": len(data), "sha256": trial.sha(data)}
                         for path, data, _ in material]}
    return material, binding


def check_staged():
    identity, deployment, _ = preflight()
    receipt = candidate_boot.verify_candidate(Path("/"), candidate_boot.BASELINE_RELEASE, identity, deployment, VARIANT)
    validate_dtb_delta((ESP / (PREFIX + "/t6050-j716s.dtb")).read_bytes())
    require(trial.digest(HELPER) == trial.digest(SUPPORT / "candidate_boot.py"), "installed trial validator differs")
    print("STAGED AND UNARMED: " + receipt["kernel_release"])
    print("No reboot, enrollment, enclave-storage write, or default boot change performed.")


def update_validator():
    """Install the repository trial validator after it admits the staged candidate; roll back on failure."""
    identity, deployment, _ = preflight()
    receipt = candidate_boot.verify_candidate(Path("/"), candidate_boot.BASELINE_RELEASE, identity, deployment, VARIANT)
    validate_dtb_delta((ESP / (PREFIX + "/t6050-j716s.dtb")).read_bytes())
    trial.check_regular(HELPER)
    before, after = HELPER.read_bytes(), (SUPPORT / "candidate_boot.py").read_bytes()
    if before == after:
        print("Installed trial validator already matches; no files changed.")
        return
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = WORK.lstat()
    require(not WORK.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o077, "unsafe staging workspace")
    backup = WORK / ("candidate_boot-" + trial.sha(before) + ".py")
    if not backup.exists():
        trial.atomic(backup, before)
    require(backup.read_bytes() == before, "validator backup differs")
    try:
        trial.atomic(HELPER, after, 0o644)
        subprocess.run([sys.executable, str(GUARD), "--check-only"], check=True)
    except BaseException:
        trial.atomic(HELPER, before, 0o644)
        raise
    print("UPDATED trial validator; it admits staged " + receipt["kernel_release"] + " and the boot guard check passed.")
    print("Previous validator retained at " + str(backup) + "; no boot selection, marker or reboot change.")


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
    record = {"schema": 1, "kind": "j716s-" + VARIANT + "-arming",
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
    scope = "bounded sensor authentication probe" if VARIANT == "sep-sensor" else "non-writing discovery only"
    print("Backup status: " + backup_status + "; " + scope + ", no enrollment.")
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


def stage(apply, package=None, provenance_sha256=None):
    identity, deployment, device = preflight()
    snapshot = protected_snapshot()
    release = candidate_boot.VARIANTS[VARIANT]["release"]
    destination = Path("/usr/lib/modules") / release
    material, binding = sensor_material(package, provenance_sha256) if VARIANT == "sep-sensor" else ([], None)
    for path in (RECEIPT, ESP / PREFIX, ESP / (PREFIX + ".bin"), destination, *(item[0] for item in material)):
        require(not path.exists() and not path.is_symlink(), "candidate path already exists: " + str(path))
    trial.check_regular(HELPER)
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = WORK.lstat()
    require(not WORK.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o077, "unsafe staging workspace")
    with tempfile.TemporaryDirectory(dir=WORK) as temporary:
        files, receipt, modules = trial.build_candidate(temporary, identity, deployment, VARIANT)
        if binding is not None:
            receipt["sensor_firmware"] = binding
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
        created_material, created_directories = [], []
        try:
            created_modules = True
            shutil.copytree(modules, destination)
            (ESP / PREFIX).mkdir()
            created_esp = True
            for name, data in files:
                trial.atomic(ESP / name, data)
            for path, data, mode in material:
                missing = [parent for parent in reversed(path.parents) if not parent.exists()]
                for directory in missing:
                    directory.mkdir(mode=0o700)
                    created_directories.append(directory)
                created_material.append(path)
                trial.atomic(path, data, mode)
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
            for path in material:
                subprocess.run(["sync", "-f", str(path[0])], check=True)
        except BaseException:
            if helper_changed:
                trial.atomic(HELPER, helper_before, 0o644)
            RECEIPT.unlink(missing_ok=True)
            (ESP / (PREFIX + ".bin")).unlink(missing_ok=True)
            if created_esp:
                shutil.rmtree(ESP / PREFIX)
            if created_modules:
                shutil.rmtree(destination)
            for path in created_material:
                path.unlink(missing_ok=True)
            for directory in reversed(created_directories):
                directory.rmdir()
            raise
    print("STAGED AND UNARMED: " + release)
    print("Default boot payload, rescue, boot dependencies, guard and spent trial marker preserved.")
    print("No reboot performed. SEP loading requires the later attended " + VARIANT + " experiment.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(candidate_boot.ONE_SHOT_PARAMETERS), default="sep-probe")
    parser.add_argument("--sensor-package", type=Path, help="private package from prepare-sensor-firmware.py (sep-sensor staging)")
    parser.add_argument("--sensor-provenance-sha256", help="separately retained provenance SHA256 of that package")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stage", action="store_true", help="install the isolated candidate, without arming")
    modes.add_argument("--check-staged", action="store_true", help="validate the already staged, unarmed candidate")
    modes.add_argument("--arm", action="store_true", help="arm the verified SEP candidate once for an attended cold boot")
    modes.add_argument("--cancel", action="store_true", help="disarm only this SEP trial without requiring candidate modules")
    modes.add_argument("--update-validator", action="store_true",
                       help="install the repository trial validator after it admits this staged candidate")
    parser.add_argument("--acknowledge-risk", action="store_true", help="acknowledge experimental SEP/boot recovery risks")
    parser.add_argument("--attended-cold-boot", action="store_true", help="attest saved work and physical recovery access")
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument("--backup-verified", action="store_true", help="attest a current verified backup and retained macOS")
    backup.add_argument("--waive-backup", action="store_true", help="explicitly accept proceeding without the recommended backup")
    args = parser.parse_args()
    select(args.variant)
    staging = not (args.arm or args.cancel or args.check_staged or args.update_validator)
    sensor = staging and args.variant == "sep-sensor"
    require(sensor == bool(args.sensor_package) == bool(args.sensor_provenance_sha256),
            "sensor package and provenance anchor are required exactly for sep-sensor staging")
    require(not (args.stage or args.arm) or args.acknowledge_risk, "staging/arming requires --acknowledge-risk")
    require(not args.arm or (args.attended_cold_boot and (args.backup_verified or args.waive_backup)),
            "arming requires attended cold boot and explicit backup attestation or waiver")
    if args.arm:
        arm("owner-attested-verified" if args.backup_verified else "waived-by-owner")
    elif args.cancel:
        cancel()
    elif args.check_staged:
        check_staged()
    elif args.update_validator:
        update_validator()
    else:
        stage(args.stage, args.sensor_package, args.sensor_provenance_sha256)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
