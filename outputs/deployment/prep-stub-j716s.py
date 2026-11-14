#!/usr/bin/env python3
"""Fresh J716s stub/ESP/Linux preparation; default read-only.

Disk operations and stub layout derive from betizzel/omarchy-m5
7974606ab1de0d41737f2c8784a7e86987612ce1 scripts/prep-stub.sh.
--apply requires sudo, a recorded volume owner and fresh backup confirmation.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

import preflight
from partition_metadata import added_partition, parts, validate_layout

HERE = Path(__file__).resolve().parent
DISKUTIL = "/usr/sbin/diskutil"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def execute(*args):
    print("==>", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def save(path, data):
    with path.open("x") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def apfs():
    return preflight.plist(DISKUTIL, "apfs", "list", "-plist")["Containers"]


def partition(whole, guid):
    listing = preflight.plist(DISKUTIL, "list", "-plist", whole)
    entries = parts(listing, whole)
    require(guid.upper() in entries, "Recorded partition GUID is absent")
    return entries[guid.upper()]


def container_for(guid):
    matches = [c for c in apfs() if any(p.get("DiskUUID", "").upper() == guid.upper()
                                     for p in c.get("PhysicalStores", []))]
    require(len(matches) == 1 and len(matches[0]["PhysicalStores"]) == 1,
            "No unique single-store container for recorded partition GUID")
    return matches[0]


def role_volume(container, role):
    matches = [v for v in container["Volumes"] if v.get("Roles") == [role]]
    require(len(matches) == 1, "Expected exactly one " + role + " volume")
    return matches[0]


def volume_info(guid):
    matches = [v for c in apfs() for v in c["Volumes"]
               if v.get("APFSVolumeUUID", "").upper() == guid.upper()]
    require(len(matches) == 1, "No unique APFS volume for recorded UUID")
    info = preflight.plist(DISKUTIL, "info", "-plist", matches[0]["DeviceIdentifier"])
    require(info.get("VolumeUUID", "").upper() == guid.upper(), "APFS volume identity changed")
    return info


def mounted(guid):
    info = volume_info(guid)
    if not info.get("MountPoint"):
        execute(DISKUTIL, "mount", info["DeviceIdentifier"])
        info = volume_info(guid)
    raw = info.get("MountPoint")
    require(raw and raw != "Not mounted", "No mount point for recorded volume")
    path = Path(raw)
    require(path.is_dir() and path.resolve() == path, "Ambiguous mount path")
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_entries(source):
    """Regular files plus relative symlinks that stay inside a source tree."""
    require(source.exists() and not source.is_symlink(), "Missing or symlinked source asset: " + str(source))
    if not source.is_dir():
        require(source.is_file(), "Source asset is not a regular file: " + str(source))
        return [source], {}
    root = source.resolve()
    files, links = [], {}
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        for name in dirnames + filenames:
            path = Path(directory) / name
            if path.is_symlink():
                target = os.readlink(path)
                resolved = (path.parent / target).resolve()
                require(not os.path.isabs(target) and resolved.exists()
                        and (resolved == root or root in resolved.parents),
                        "Symlink escapes source assets: " + str(path))
                links[path.relative_to(source)] = target
            elif name in filenames:
                require(path.is_file(), "Unexpected special file in source assets: " + str(path))
                files.append(path)
    require(bool(files), "Source assets have no files")
    return sorted(files), links


def source_files(source):
    return source_entries(source)[0]


def verified_copy(source, target, prefix, hashes, resume=False):
    files, links = source_entries(source)
    expected = {str(file.relative_to(source) if source.is_dir() else Path(".")): digest(file) for file in files}
    if target.exists():
        # Only a continuation may accept an existing copy, and only if it is an exact tree match.
        require(resume, "Fresh copy destination already exists: " + str(target))
        copied_files, copied_links = source_entries(target)
        copied_names = {str(file.relative_to(target) if target.is_dir() else Path(".")) for file in copied_files}
        require(copied_names == set(expected) and copied_links == links,
                "Existing copy is incomplete or has extra entries: " + str(target))
        print("==> verifying existing copy", target, flush=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        execute("/usr/bin/ditto", source, target)
    for relative, expected_hash in expected.items():
        file = target / relative if source.is_dir() else target
        require(file.is_file() and not file.is_symlink() and digest(file) == expected_hash,
                "Copied asset differs: " + str(file))
        key = str(Path(prefix) / relative) if source.is_dir() else prefix
        hashes[key] = expected_hash
    for relative, link_target in links.items():
        copied = target / relative
        require(copied.is_symlink() and os.readlink(copied) == link_target, "Copied symlink differs: " + str(copied))
        hashes[str(Path(prefix) / relative)] = "symlink:" + link_target


def create_partition(whole, after_guid, kind, disk_type, name, size, journal, label):
    before = preflight.plist(DISKUTIL, "list", "-plist", whole)
    (journal / ("before-" + label + ".plist")).write_bytes(plistlib.dumps(before))
    after_device = partition(whole, after_guid)["DeviceIdentifier"]
    execute(DISKUTIL, "addPartition", after_device, disk_type, name, str(size) + "B")
    after = preflight.plist(DISKUTIL, "list", "-plist", whole)
    (journal / ("after-" + label + ".plist")).write_bytes(plistlib.dumps(after))
    receipt = added_partition(before, after, whole, kind, size)
    receipt["timestamp_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(journal / (label + "-partition.json"), receipt)
    return receipt["partition_uuid"]


def source_assets(report):
    """Re-validate the booted source group's paired assets; return what population needs."""
    source, layout = report["source"], report["proposed_layout"]
    source_container = container_for(source["partition_uuid"])
    pb_uuid = role_volume(source_container, "Preboot")["APFSVolumeUUID"]
    rec_uuid = role_volume(source_container, "Recovery")["APFSVolumeUUID"]
    group = source["booted_volume_group"]
    pb = mounted(pb_uuid) / group
    rec = mounted(rec_uuid) / group
    system_version = Path("/System/Library/CoreServices/SystemVersion.plist")
    version = plistlib.loads(system_version.read_bytes())
    require(version["ProductBuildVersion"] == preflight.EXPECTED_BUILD, "Running SystemVersion changed")
    manifest = plistlib.loads((pb / "restore/BuildManifest.plist").read_bytes())
    require(preflight.manifest_build(manifest) == preflight.EXPECTED_BUILD, "Source restore build changed")
    require((pb / "var/db/AdminUserRecoveryInfo.plist").is_file(), "Missing source owner credentials")
    require((pb / "restore/usr/standalone/bootcaches.plist").is_file(), "Missing source bootcaches")
    require((rec / "usr/standalone/firmware/arm64eBaseSystem.dmg").is_file(), "Missing paired Recovery image")
    admin = plistlib.loads((pb / "var/db/AdminUserRecoveryInfo.plist").read_bytes())
    require(isinstance(admin, dict) and bool(admin), "No source owner credentials")
    assets = [pb / "restore", pb / "var/db/AdminUserRecoveryInfo.plist", rec, system_version]
    asset_bytes = sum(file.stat().st_size for asset in assets for file in source_files(asset))
    owner = os.environ.get("SUDO_USER")
    require(owner and owner in admin, "Run sudo as an actual owner recorded in the source Recovery assets")
    require(asset_bytes < layout["stub_bytes"] - 512 * 1024**2, "Paired assets exceed fresh stub capacity")
    return {"pb_uuid": pb_uuid, "rec_uuid": rec_uuid, "group": group, "system_version": system_version}


def esp_boot_sector(device):
    with open("/dev/r" + device, "rb") as handle:
        return handle.read(4096)[:512]


def require_unformatted_esp(device):
    """diskutil reports FilesystemType msdos for every EFI-typed partition, so inspect the boot sector."""
    info = preflight.plist(DISKUTIL, "info", "-plist", device)
    require(not info.get("VolumeUUID") and not info.get("MountPoint"),
            "New ESP has a mounted/identified filesystem; refusing formatting")
    sector = esp_boot_sector(device)
    has_fat = sector[510:512] == b"\x55\xaa" and (sector[82:87] == b"FAT32" or sector[54:57] == b"FAT")
    require(not has_fat, "New ESP already contains a FAT boot sector; refusing formatting")


def require_formatted_esp(device):
    sector = esp_boot_sector(device)
    require(sector[510:512] == b"\x55\xaa" and sector[82:87] == b"FAT32" and sector[71:78] == b"OMARCHY",
            "Recorded ESP is not the OMARCHY FAT32 filesystem this attempt created")


def populate(report, journal, baseline, sources, target, layout, resume=False):
    """Format the recorded ESP, add the Linux partition after it, and copy paired stub assets."""
    whole = report["source"]["physical_disk"]
    stub_guid, esp_guid, vgid = target["stub_partition_uuid"], target["esp_partition_uuid"], target["volume_group_uuid"]
    esp = partition(whole, esp_guid)
    require(esp["Content"] in ("EFI", "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"), "Recorded ESP has wrong type")
    linux_receipt = journal / "linux-partition.json"
    if linux_receipt.exists():
        # The Linux partition is only ever created after a successful newfs_msdos.
        require(resume, "Linux partition already recorded; use the continuation")
        require_formatted_esp(esp["DeviceIdentifier"])
        linux_guid = json.loads(linux_receipt.read_text())["partition_uuid"]
        linux = partition(whole, linux_guid)
        require(linux["Content"] in ("Linux Filesystem", "0FC63DAF-8483-4772-8E79-3D69D8477DE4") and
                abs(linux["Size"] - layout["linux_bytes"]) <= 2 * 1024**2, "Recorded Linux partition differs")
    else:
        require_unformatted_esp(esp["DeviceIdentifier"])
        execute("/sbin/newfs_msdos", "-F", "32", "-v", "OMARCHY", "/dev/r" + esp["DeviceIdentifier"])
        linux_guid = create_partition(whole, esp_guid, "Linux Filesystem", "%Linux%", "%noformat%",
                                      layout["linux_bytes"], journal, "linux")
    target["linux_partition_uuid"] = linux_guid
    mounts = {role: mounted(guid) for role, guid in target["volume_uuids"].items()}
    require(len(set(mounts.values())) == 4 and all(path not in (Path("/"), Path("/System/Volumes/Data"))
                                                 for path in mounts.values()), "Unsafe target mounts")
    group, system_version = sources["group"], sources["system_version"]
    pb, rec = mounted(sources["pb_uuid"]) / group, mounted(sources["rec_uuid"]) / group
    hashes = {}
    verified_copy(pb / "restore", mounts["Preboot"] / vgid / "restore", "Preboot/" + vgid + "/restore", hashes, resume)
    verified_copy(pb / "var/db/AdminUserRecoveryInfo.plist",
                  mounts["Preboot"] / vgid / "var/db/AdminUserRecoveryInfo.plist",
                  "Preboot/" + vgid + "/var/db/AdminUserRecoveryInfo.plist", hashes, resume)
    execute("/usr/bin/chflags", "schg", mounts["Preboot"] / vgid / "var/db/AdminUserRecoveryInfo.plist")
    verified_copy(rec, mounts["Recovery"] / vgid, "Recovery/" + vgid, hashes, resume)
    verified_copy(system_version, mounts["System"] / "System/Library/CoreServices/SystemVersion.plist",
                  "System/System/Library/CoreServices/SystemVersion.plist", hashes, resume)
    platform = system_version.with_name("PlatformSupport.plist")
    if platform.is_file():
        verified_copy(platform, mounts["System"] / "System/Library/CoreServices/PlatformSupport.plist",
                      "System/System/Library/CoreServices/PlatformSupport.plist", hashes, resume)
    verified_copy(pb / "restore/usr/standalone/bootcaches.plist", mounts["System"] / "usr/standalone/bootcaches.plist",
                  "System/usr/standalone/bootcaches.plist", hashes, resume)
    copied_manifest = mounts["Preboot"] / vgid / "restore/BuildManifest.plist"
    require(preflight.manifest_build(plistlib.loads(copied_manifest.read_bytes())) == preflight.EXPECTED_BUILD,
            "Copied restore build differs")
    final_parts = parts(preflight.plist(DISKUTIL, "list", "-plist", whole), whole)
    validate_layout(target, final_parts)
    target.update(copy_sha256=hashes, source_build=preflight.EXPECTED_BUILD,
                  source_firmware=report["machine"]["firmware"], status="populated; not armed or enrolled")
    save(journal / "target.json", target)
    print("Populated fresh stub. Receipt:", journal / "target.json")
    print("Linux space is unformatted; loader preparation/enrollment and hardware qualification are separate.")


def apply(report):
    require(os.geteuid() == 0, "Run --apply with sudo only after exact staging macOS is ready")
    require(report["ready_for_partition_and_stub_setup"],
            "Fresh setup refused: " + str(report["blocking_checks"]))
    print(json.dumps(report["proposed_layout"], indent=2))
    backup = preflight.confirm_backup()
    require(input("Create this fresh layout and copy stub assets? Type CREATE: ") == "CREATE", "Aborted")
    # Recheck after physical confirmation; never apply an earlier saved plan.
    live = preflight.run(report["proposed_layout"]["linux_bytes"] // 1_000_000_000)
    preflight.revalidate_source(report, live)
    require(live["ready_for_partition_and_stub_setup"] and live["baseline_partitions"] == report["baseline_partitions"],
            "Fresh partition baseline changed during confirmation")
    report = live
    source, layout = report["source"], report["proposed_layout"]
    whole, source_guid = source["physical_disk"], source["partition_uuid"]
    journal = Path(tempfile.mkdtemp(prefix="stub-attempt.", dir=HERE))
    save(journal / "preflight.json", report)
    print("Attempt journal:", journal, flush=True)
    sources = source_assets(report)
    group = sources["group"]
    preflight.revalidate_source(report)
    baseline = parts(preflight.plist(DISKUTIL, "list", "-plist", whole), whole)
    require(baseline == report["baseline_partitions"], "Fresh baseline changed while validating paired assets")
    save(journal / "baseline-partitions.json", baseline)
    execute(DISKUTIL, "apfs", "resizeContainer", partition(whole, source_guid)["DeviceIdentifier"],
            str(layout["macos_container_target_bytes"]) + "B")
    resized = parts(preflight.plist(DISKUTIL, "list", "-plist", whole), whole)
    require(set(resized) == set(baseline), "Partition GUID set changed during resize")
    for guid, original in baseline.items():
        require(resized[guid]["Content"] == original["Content"], "Partition type changed during resize")
        expected_size = layout["macos_container_target_bytes"] if guid == source_guid else original["Size"]
        require(abs(resized[guid]["Size"] - expected_size) <= (2 * 1024**2 if guid == source_guid else 0),
                "Unexpected source/protected partition size after resize")
    stub_guid = create_partition(whole, source_guid, "Apple_APFS", "apfs", "Omarchy",
                                 layout["stub_bytes"], journal, "stub")
    stub = container_for(stub_guid)
    require(stub["APFSContainerUUID"] != source["container_uuid"], "Stub aliases source container")
    require(len(stub["Volumes"]) == 1 and not stub["Volumes"][0].get("Roles"),
            "Fresh APFS partition does not have exactly one no-role volume")
    data = stub["Volumes"][0]
    execute(DISKUTIL, "apfs", "changeVolumeRole", data["DeviceIdentifier"], "D")
    data = role_volume(container_for(stub_guid), "Data")
    execute(DISKUTIL, "rename", data["DeviceIdentifier"], "Omarchy - Data")
    execute(DISKUTIL, "apfs", "addVolume", container_for(stub_guid)["ContainerReference"],
            "apfs", "Omarchy", "-role", "S", "-groupWith", data["DeviceIdentifier"])
    for role, flag in (("Preboot", "B"), ("Recovery", "R")):
        stub = container_for(stub_guid)
        if not any(role in v.get("Roles", []) for v in stub["Volumes"]):
            execute(DISKUTIL, "apfs", "addVolume", stub["ContainerReference"], "apfs", role, "-role", flag)
    stub = container_for(stub_guid)
    require(len(stub["Volumes"]) == 4, "Unexpected fresh stub volume count; review journal, never resume")
    volumes = {role: role_volume(stub, role) for role in ("System", "Data", "Preboot", "Recovery")}
    sys_info = volume_info(volumes["System"]["APFSVolumeUUID"])
    data_info = volume_info(volumes["Data"]["APFSVolumeUUID"])
    vgid = sys_info.get("APFSVolumeGroupID")
    require(vgid and vgid == data_info.get("APFSVolumeGroupID") and vgid != group,
            "Stub System/Data grouping is invalid")
    target = {"schema": 2, "stub_partition_uuid": stub_guid, "system_volume_uuid": sys_info["VolumeUUID"],
              "volume_group_uuid": vgid, "source_volume_group_uuid": group,
              "source_partition_uuid": source_guid, "container_uuid": stub["APFSContainerUUID"],
              "source": source, "baseline_partitions": baseline, "proposed_layout": layout,
              "backup_confirmation": backup,
              "volume_uuids": {role: v["APFSVolumeUUID"] for role, v in volumes.items()}}
    save(journal / "stub-volumes.json", target)
    target["esp_partition_uuid"] = create_partition(whole, stub_guid, "EFI", "%EFI%", "%noformat%",
                                                    layout["esp_bytes"], journal, "esp")
    populate(report, journal, baseline, sources, target, layout)


def continue_attempt(journal, authorized=False):
    """Finish one recorded attempt that stopped after its ESP; every recorded identity must still hold."""
    require(authorized, "--continue-attempt requires explicit --apply")
    require(os.geteuid() == 0, "Run --continue-attempt with sudo")
    backup = preflight.confirm_backup()
    journal = journal.resolve()
    require(journal.parent == HERE and journal.name.startswith("stub-attempt.") and journal.is_dir(),
            "Journal must be a stub-attempt directory beside this script")
    for name in ("preflight.json", "stub-partition.json", "stub-volumes.json", "esp-partition.json"):
        require((journal / name).is_file(), "Journal lacks " + name)
    require(not (journal / "target.json").exists(), "Attempt already completed")
    report = json.loads((journal / "preflight.json").read_text())
    layout, source = report["proposed_layout"], report["source"]
    whole, source_guid = source["physical_disk"], source["partition_uuid"]
    target = json.loads((journal / "stub-volumes.json").read_text())
    stub_receipt = json.loads((journal / "stub-partition.json").read_text())
    esp_receipt = json.loads((journal / "esp-partition.json").read_text())
    require(stub_receipt["partition_uuid"] == target["stub_partition_uuid"], "Stub receipts disagree")
    live = preflight.revalidate_source(report)
    whole = live["source"]["physical_disk"]
    report["source"]["physical_disk"] = whole
    target["source"]["physical_disk"] = whole
    baseline = report["baseline_partitions"]
    current = parts(preflight.plist(DISKUTIL, "list", "-plist", whole), whole)
    stub_guid, esp_guid = target["stub_partition_uuid"], esp_receipt["partition_uuid"]
    target["esp_partition_uuid"] = esp_guid
    if (journal / "linux-partition.json").exists():
        target["linux_partition_uuid"] = json.loads((journal / "linux-partition.json").read_text())["partition_uuid"]
    validate_layout(target, current, allow_missing_linux=True)
    require(abs(current[esp_guid]["Size"] - layout["esp_bytes"]) <= 2 * 1024**2, "Recorded ESP size differs")
    stub = container_for(stub_guid)
    require(stub["APFSContainerUUID"] == target["container_uuid"], "Stub container identity changed")
    live_volumes = {role: role_volume(stub, role)["APFSVolumeUUID"] for role in ("System", "Data", "Preboot", "Recovery")}
    require(len(stub["Volumes"]) == 4 and live_volumes == target["volume_uuids"], "Stub volumes changed")
    require(volume_info(live_volumes["System"]).get("APFSVolumeGroupID") == target["volume_group_uuid"],
            "Stub volume group changed")
    stamp = datetime.datetime.now(datetime.timezone.utc)
    save(journal / ("continued-" + stamp.strftime("%Y%m%dT%H%M%SZ") + ".json"),
         {"timestamp_utc": stamp.isoformat(), "live_preflight": live,
          "backup_confirmation": backup, "reason": "Continue only the recorded post-ESP preparation attempt"})
    target["esp_partition_uuid"] = esp_guid
    target["backup_confirmation"] = backup
    populate(report, journal, baseline, source_assets(report), target, layout, resume=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="read-only preflight (default)")
    modes.add_argument("--apply", action="store_true", help="explicit mutation; sudo owner and fresh backup confirmation required")
    parser.add_argument("--continue-attempt", type=Path, metavar="JOURNAL",
                        help="with --apply only: complete the exact recorded post-ESP attempt")
    parser.add_argument("--linux-gb", type=int, default=150, help="32..250 decimal GB; fresh proposal only")
    args = parser.parse_args()
    if not 32 <= args.linux_gb <= 250:
        parser.error("--linux-gb must be 32..250")
    try:
        if args.continue_attempt:
            continue_attempt(args.continue_attempt, authorized=args.apply)
            return 0
        report = preflight.run(args.linux_gb)
        (HERE / "preflight-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        if args.apply:
            apply(report)
        return 0 if report["ready_for_partition_and_stub_setup"] else 1
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("ERROR:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
