#!/usr/bin/env python3
"""Read-only J716s deployment preflight. Writes one JSON report; no apply mode."""
import argparse
import datetime
import json
import pathlib
import plistlib
import subprocess

from partition_metadata import parts


def canonical_uuid(value):
    import uuid
    return str(uuid.UUID(value)).upper()


EXPECTED_BUILD = "26C5035r"
EXPECTED_FIRMWARE = "mBoot-20457.60.65.0.1"


def command(*args):
    result = subprocess.run(args, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError(" ".join(args) + ": " + result.stderr.decode().strip())
    return result.stdout


def plist(*args):
    return plistlib.loads(command(*args))


def manifest_build(manifest):
    """Require every board identity to agree; never discard missing builds."""
    identities = [i for i in manifest.get("BuildIdentities", [])
                  if i.get("Info", {}).get("DeviceClass", "").lower() == "j716sap"]
    if not identities:
        raise ValueError("No J716s restore identity")
    top = manifest.get("ProductBuildVersion")
    builds = {i.get("Info", {}).get("BuildNumber") for i in identities}
    if builds == {None} and top:
        return top
    if len(builds) != 1 or not next(iter(builds)):
        raise ValueError("Missing or conflicting J716s identity builds")
    build = next(iter(builds))
    if top and top != build:
        raise ValueError("Top-level build differs from J716s identity")
    return build


def boot_firmware():
    chosen = plist("/usr/sbin/ioreg", "-a", "-p", "IODeviceTree", "-n", "chosen", "-r", "-d", "1")
    if len(chosen) != 1:
        raise ValueError("No unique chosen firmware node")
    def text(key):
        value = chosen[0].get(key)
        if isinstance(value, bytes):
            return value.split(b"\0", 1)[0].decode("ascii")
        return value if isinstance(value, str) else None
    return {"system": text("system-firmware-version"), "booted_pairing": text("firmware-version")}


def run(linux_gb):
    root = plist("/usr/sbin/diskutil", "info", "-plist", "/")
    apfs = plist("/usr/sbin/diskutil", "apfs", "list", "-plist")
    matches = [c for c in apfs["Containers"]
               if c["ContainerReference"] == root.get("APFSContainerReference")]
    if len(matches) != 1 or not root.get("APFSVolumeGroupID"):
        raise ValueError("Running root does not identify one APFS System/Data group")
    container = matches[0]
    stores = container["PhysicalStores"]
    if len(stores) != 1:
        raise RuntimeError("Expected exactly one physical store; no plan produced")
    store = stores[0]["DeviceIdentifier"]
    store_info = plist("/usr/sbin/diskutil", "info", "-plist", store)
    whole = store_info["ParentWholeDisk"]
    physical = plist("/usr/sbin/diskutil", "info", "-plist", whole)
    listing = plist("/usr/sbin/diskutil", "list", "-plist", whole)
    partitions = next(d["Partitions"] for d in listing["AllDisksAndPartitions"]
                      if d["DeviceIdentifier"] == whole)
    baseline = parts(listing, whole)
    all_physical = plist("/usr/sbin/diskutil", "list", "-plist", "physical")
    internal_ssds = []
    for disk in all_physical["AllDisksAndPartitions"]:
        info = plist("/usr/sbin/diskutil", "info", "-plist", disk["DeviceIdentifier"])
        if info.get("Internal") is True and info.get("SolidState") is True and info.get("WholeDisk") is True:
            internal_ssds.append(info["DeviceIdentifier"])
    limits = plist("/usr/sbin/diskutil", "apfs", "resizeContainer", store, "limits", "-plist")
    model = command("/usr/sbin/sysctl", "-n", "hw.model").decode().strip()
    build = command("/usr/bin/sw_vers", "-buildVersion").decode().strip()
    power = command("/usr/bin/pmset", "-g", "batt").decode()
    firmware = boot_firmware()
    source_group = canonical_uuid(root["APFSVolumeGroupID"])
    paired = {"System": [], "Data": []}
    for volume in container["Volumes"]:
        for role in paired:
            if volume.get("Roles") == [role]:
                info = plist("/usr/sbin/diskutil", "info", "-plist", volume["DeviceIdentifier"])
                if str(info.get("APFSVolumeGroupID", "")).upper() == source_group:
                    paired[role].append({"device": info["DeviceIdentifier"],
                                         "uuid": canonical_uuid(info["VolumeUUID"]),
                                         "name": info.get("VolumeName")})
    systems, data_volumes = paired["System"], paired["Data"]
    data_root = plist("/usr/sbin/diskutil", "info", "-plist", "/System/Volumes/Data")
    preboots = [v for v in container["Volumes"] if v.get("Roles") == ["Preboot"]]
    recoveries = [v for v in container["Volumes"] if v.get("Roles") == ["Recovery"]]
    source_partuuid = canonical_uuid(store_info["DiskUUID"])
    isc = [p for p in partitions if p.get("Content") == "Apple_APFS_ISC"]
    recovery_parts = [p for p in partitions if p.get("Content") == "Apple_APFS_Recovery"]
    pb = None
    manifest_path = None
    restore_build = None
    restore_error = None
    if len(preboots) == 1 and source_group:
        pb = plist("/usr/sbin/diskutil", "info", "-plist", preboots[0]["DeviceIdentifier"])
        if pb.get("MountPoint"):
            manifest_path = str(pathlib.Path(pb["MountPoint"]) / source_group / "restore/BuildManifest.plist")
            try:
                with open(manifest_path, "rb") as handle:
                    manifest = plistlib.load(handle)
                restore_build = manifest_build(manifest)
            except (OSError, ValueError, plistlib.InvalidFileException) as exc:
                restore_error = str(exc)
    checks = {}
    checks["model_matches"] = model == "Mac17,8"
    checks["single_internal_solid_state_store"] = (internal_ssds == [whole]
        and physical.get("Internal") is True and physical.get("SolidState") is True
        and physical.get("WholeDisk") is True)
    checks["source_partition_container_consistent"] = (store_info.get("Content") == "Apple_APFS"
        and canonical_uuid(stores[0]["DiskUUID"]) == source_partuuid
        and source_partuuid in baseline)
    checks["fresh_partition_layout_matches"] = (len(partitions) == 3
        and [p.get("Content") for p in partitions] == ["Apple_APFS_ISC", "Apple_APFS", "Apple_APFS_Recovery"]
        and canonical_uuid(partitions[1]["DiskUUID"]) == source_partuuid
        and len(isc) == len(recovery_parts) == 1)
    checks["one_system_data_pair_in_booted_group"] = (len(systems) == len(data_volumes) == 1
        and root.get("APFSContainerReference") == data_root.get("APFSContainerReference")
        and str(data_root.get("APFSVolumeGroupID", "")).upper() == source_group
        and data_volumes[0]["uuid"] == str(data_root.get("VolumeUUID", "")).upper())
    checks["one_source_preboot_and_recovery"] = len(preboots) == len(recoveries) == 1
    checks["system_firmware_matches"] = firmware["system"] == EXPECTED_FIRMWARE
    checks["booted_firmware_pairing_matches"] = firmware["booted_pairing"] == EXPECTED_FIRMWARE
    checks["running_exact_candidate_build"] = build == EXPECTED_BUILD
    checks["restore_matches_running_exact_build"] = restore_build == build == EXPECTED_BUILD
    checks["ac_power"] = "Now drawing from 'AC Power'" in power
    # All sizes are decimal bytes, except explicit alignment headroom.
    stub_bytes, esp_bytes, alignment = 6_000_000_000, 500_000_000, 64 * 1024**2
    linux_bytes = linux_gb * 1_000_000_000
    current = container["CapacityCeiling"]
    target = (current - stub_bytes - esp_bytes - linux_bytes - alignment) // 1024**2 * 1024**2
    preferred = limits.get("MinimumSizePreferred", limits.get("MinimumSizeNoGuard"))
    checks["proposed_size_above_preferred_minimum"] = preferred is not None and target >= preferred
    post_resize_free = container["CapacityFree"] - (current - target)
    checks["proposed_macos_free_at_least_100GB"] = post_resize_free >= 100_000_000_000
    return {
        "schema": 2,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "read-only; report only; no mutation or mount commands",
        "machine": {"model": model, "running_build": build, "firmware": firmware,
                    "filevault": command("/usr/bin/fdesetup", "status").decode().strip()},
        "source": {"physical_disk": whole, "physical_store": store, "partition_uuid": source_partuuid,
                   "container": container["ContainerReference"], "container_uuid": canonical_uuid(container["APFSContainerUUID"]),
                   "booted_volume_group": source_group, "matching_system_volumes": systems,
                   "system_volume_uuid": systems[0]["uuid"] if len(systems) == 1 else None,
                   "data_volume_uuid": data_volumes[0]["uuid"] if len(data_volumes) == 1 else None,
                   "preboot_device": preboots[0]["DeviceIdentifier"] if len(preboots) == 1 else None,
                   "preboot_volume_uuid": canonical_uuid(preboots[0]["APFSVolumeUUID"]) if len(preboots) == 1 else None,
                   "preboot_mount": pb.get("MountPoint") if pb else None,
                   "recovery_device": recoveries[0]["DeviceIdentifier"] if len(recoveries) == 1 else None,
                   "recovery_volume_uuid": canonical_uuid(recoveries[0]["APFSVolumeUUID"]) if len(recoveries) == 1 else None,
                   "isc_partition_uuid": canonical_uuid(isc[0]["DiskUUID"]) if len(isc) == 1 else None,
                   "recovery_partition_uuid": canonical_uuid(recovery_parts[0]["DiskUUID"]) if len(recovery_parts) == 1 else None,
                   "restore_manifest": manifest_path, "restore_build": restore_build, "restore_read_error": restore_error},
        "disk": {"physical_bytes": physical["TotalSize"], "container_bytes": current,
                 "free_bytes": container["CapacityFree"], "preferred_minimum_bytes": preferred,
                 "partitions": [{k: p.get(k) for k in ("DeviceIdentifier", "Content", "DiskUUID", "Size")} for p in partitions]},
        "baseline_partitions": baseline,
        "proposed_layout": {"status": "fresh proposal; revalidated immediately before apply",
                            "linux_bytes": linux_bytes, "stub_bytes": stub_bytes, "esp_bytes": esp_bytes,
                            "alignment_headroom_bytes": alignment, "macos_container_target_bytes": target,
                            "macos_free_after_resize_at_current_usage_bytes": post_resize_free},
        "checks": checks,
        "ready_for_partition_and_stub_setup": all(checks.values()),
        "blocking_checks": [key for key, value in checks.items() if not value],
        "not_established": ["installer signature/build verification", "owner/admin authentication",
                            "matching paired stub firmware", "actual ESP identity and filled Stage 1",
                            "physical Linux boot", "data recovery copy availability"],
    }


SOURCE_ID_FIELDS = ("partition_uuid", "container_uuid", "booted_volume_group", "system_volume_uuid",
                    "data_volume_uuid", "preboot_volume_uuid", "recovery_volume_uuid",
                    "isc_partition_uuid", "recovery_partition_uuid")
LIVE_CHECKS = ("model_matches", "single_internal_solid_state_store", "source_partition_container_consistent",
               "one_system_data_pair_in_booted_group", "one_source_preboot_and_recovery",
               "system_firmware_matches", "booted_firmware_pairing_matches", "running_exact_candidate_build",
               "restore_matches_running_exact_build", "ac_power")


def revalidate_source(recorded, live=None):
    """Re-discover the running source; device numbers and mount names are not identities."""
    live = run(recorded["proposed_layout"]["linux_bytes"] // 1_000_000_000) if live is None else live
    for key in LIVE_CHECKS:
        if live["checks"].get(key) is not True:
            raise ValueError("Live source check failed: " + key)
    for key in SOURCE_ID_FIELDS:
        if canonical_uuid(live["source"][key]) != canonical_uuid(recorded["source"][key]):
            raise ValueError("Recorded source identity changed: " + key)
    return live


def confirm_backup():
    """A new physical-operator assertion, never a saved waiver or command-line bypass."""
    import sys
    if not sys.stdin.isatty():
        raise ValueError("Destructive actions require an interactive physical-owner Terminal")
    answer = input("Confirm a current, independently recoverable backup of this Mac. Type BACKUP VERIFIED: ")
    if answer != "BACKUP VERIFIED":
        raise ValueError("Fresh backup confirmation required; no waiver supported")
    return {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "statement": "current independently recoverable backup verified by operator"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only preflight (the only mode and default)")
    parser.add_argument("--linux-gb", type=int, default=150, help="proposed Linux partition, decimal GB")
    parser.add_argument("--output", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().with_name("preflight-report.json"))
    args = parser.parse_args()
    if not 32 <= args.linux_gb <= 250:
        parser.error("--linux-gb must be 32..250 for this machine-specific proposal")
    try:
        report = run(args.linux_gb)
    except (RuntimeError, KeyError, StopIteration, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report = {"mode": "read-only; incomplete report", "ready_for_partition_and_stub_setup": False, "error": str(exc)}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report.get("ready_for_partition_and_stub_setup") else 1)
