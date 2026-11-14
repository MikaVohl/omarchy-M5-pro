#!/usr/bin/env python3
"""Pure partition-GUID comparison used before formatting a fresh target."""
import uuid

TYPE_GUIDS = {"Linux Filesystem": "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
              "EFI": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
              "Apple_APFS": "7C3457EF-0000-11AA-AA11-00306543ECAC"}


def parts(data, whole):
    disks = [d for d in data["AllDisksAndPartitions"] if d["DeviceIdentifier"] == whole]
    if len(disks) != 1:
        raise ValueError("Expected exactly one physical disk metadata entry")
    result = {}
    for part in disks[0]["Partitions"]:
        if "free" in str(part.get("Content", "")).lower() and not part.get("DeviceIdentifier"):
            continue
        guid = str(uuid.UUID(part["DiskUUID"])).upper()
        if guid in result or not part.get("DeviceIdentifier"):
            raise ValueError("Duplicate GUID or missing partition device")
        result[guid] = part
    return result


def added_partition(before, after, whole, expected_type, expected_size):
    old, new = parts(before, whole), parts(after, whole)
    if not set(old) <= set(new):
        raise ValueError("An existing partition GUID disappeared")
    for guid in old:
        if any(old[guid][key] != new[guid][key] for key in ("Content", "Size")):
            raise ValueError("An existing partition changed type or size during addPartition")
    added = set(new) - set(old)
    if len(added) != 1:
        raise ValueError("Expected exactly one added partition GUID")
    guid = added.pop()
    part = new[guid]
    if part["Content"] not in (expected_type, TYPE_GUIDS[expected_type]):
        raise ValueError("New partition has unexpected type: " + part["Content"])
    if abs(part["Size"] - expected_size) > 2 * 1024**2:
        raise ValueError("New partition has unexpected size")
    return {"physical_disk": whole, "partition_uuid": guid,
            "device_at_creation": part["DeviceIdentifier"], "content": part["Content"],
            "size_bytes": part["Size"]}


def validate_layout(receipt, current, allow_missing_linux=False):
    """Require the recorded Apple baseline plus only this attempt's created partitions."""
    if receipt.get("schema") != 2:
        raise ValueError("Expected fresh target receipt schema 2")
    source, baseline, layout = receipt["source"], receipt["baseline_partitions"], receipt["proposed_layout"]
    source_guid = str(uuid.UUID(source["partition_uuid"])).upper()
    protected = {str(uuid.UUID(source[key])).upper(): content for key, content in (
        ("isc_partition_uuid", "Apple_APFS_ISC"), ("recovery_partition_uuid", "Apple_APFS_Recovery"))}
    if set(baseline) != {source_guid} | set(protected) or len(baseline) != 3:
        raise ValueError("Receipt does not contain one fresh Apple partition baseline")
    expected = {source_guid: ("Apple_APFS", layout["macos_container_target_bytes"])}
    for guid, content in protected.items():
        if baseline[guid]["Content"] != content:
            raise ValueError("Protected baseline partition has wrong type")
        expected[guid] = (content, baseline[guid]["Size"])
    if baseline[source_guid]["Content"] != "Apple_APFS":
        raise ValueError("Retained source baseline partition has wrong type")
    for key, content, size in (("stub_partition_uuid", "Apple_APFS", layout["stub_bytes"]),
                               ("esp_partition_uuid", "EFI", layout["esp_bytes"]),
                               ("linux_partition_uuid", "Linux Filesystem", layout["linux_bytes"])):
        if key not in receipt and key == "linux_partition_uuid" and allow_missing_linux:
            continue
        guid = str(uuid.UUID(receipt[key])).upper()
        if guid in expected:
            raise ValueError("Created partition collides with recorded baseline/target")
        expected[guid] = (content, size)
    if set(current) != set(expected):
        raise ValueError("Partition GUID set differs from recorded attempt")
    for guid, (content, size) in expected.items():
        part = current[guid]
        tolerance = 0 if guid in protected else 2 * 1024**2
        if part["Content"] not in (content, TYPE_GUIDS.get(content, content)) or abs(part["Size"] - size) > tolerance:
            raise ValueError("Recorded partition type/size changed: " + guid)
