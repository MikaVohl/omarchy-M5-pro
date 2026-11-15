#!/usr/bin/env python3
"""Prepare/check PRIVATE J716s 26C5035r factory firmware; never install it.

prepare reads the current Mac's iBootSystemContainer O_RDONLY. A regular APFS
partition image is accepted only with --source-sha256 and only if its complete
bytes match that current partition. No mounts, writes to sources, sensor/SEP
operations, provisioning, enrollment or service setup are performed. The
upstream APFS/DER/admission code is executed only after immutable hash checks;
its installer, scanner and main entry points are never invoked.

Outputs are canonical comb containers, not bare FSC2/MSRk IMG4s or raw keys.
The private provenance report binds the boot identity, exact build, partition,
APFS checkpoint, source bytes, original records and output bytes. Keep the
reported provenance SHA256 separately: check requires that external anchor.
check validates the anchored files against the current unit/build/policy; it
does not reread the mutable ISC or require its bytes to stay unchanged at boot.
Metadata/digest checks are NOT independent Apple signature verification. Only
Apple's enclave can make that decision in a separately authorized experiment.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid

PROJECT = Path(__file__).absolute().parents[1]
KERNEL_COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
TOOL_HASHES = {
    "aurora-touchid-setup": "43b61c8ae7c0b08f033cfb5fec4515c4e946700a6142064d0e576b116931a7c1",
    "extract-mesa-calibration.py": "15a878dcf382a22c5d807f0f2c754fe70d39baec88f65aea183b1fe56cf69441",
}
RECIPE = "j716s-26C5035r-same-unit-factory-v1"
BOARD, CHIP = 12, 0x6050
BUILD = "26C5035r"
# Pinned Aura V27_3B1 maps this exact label and mBoot pair to the profile build.
FIRMWARE_LABEL = b"27.3 beta1\x00"
FIRMWARE_IBOOT = b"mBoot-20457.60.65.0.1\x00"
MODEL = "Mac17,8"
ISC_TYPE = "69646961-6700-11aa-aa11-00306543ecac"
NAMES = {"apple/mesacal-j716s.bin": b"FSC2", "apple/msrk-j716s.bin": b"MSRk"}
REPORT = "sensor-firmware-provenance.json"
MAX_RECORD = 16 * 1024 * 1024
MAX_SOURCE = 4 * 1024 * 1024 * 1024
MAX_REPORT = 1024 * 1024
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
DT = Path("/sys/firmware/devicetree/base")


class Refused(ValueError):
    """A safe, non-secret refusal suitable for stderr."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def expected_digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise Refused("expected an exact lowercase SHA256 digest")
    return value


def safe_path(path):
    # Check before normalization, so '..' cannot be silently resolved away.
    path = Path(path)
    if ".." in path.parts:
        raise Refused("path traversal is forbidden")
    return Path(os.path.abspath(path))


def open_parent(path, root_owned=False):
    """Open every ancestor without following any user-supplied symlink."""
    path = safe_path(path)
    fd = os.open("/", FLAGS | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if root_owned and (info.st_uid != 0 or info.st_mode & 0o022):
                raise Refused("runtime path is not root controlled")
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def open_input(path, private=False, root_owned=False):
    parent, name = open_parent(path, root_owned)
    try:
        fd = os.open(name, FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise Refused("input is not a single-link regular file")
    if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        os.close(fd)
        raise Refused("input is not a private file owned by this process's user")
    if root_owned and (info.st_uid != 0 or info.st_mode & 0o022):
        os.close(fd)
        raise Refused("runtime file is not root controlled")
    return fd


def read_input(path, limit, private=False, root_owned=False):
    fd = open_input(path, private, root_owned)
    try:
        size = os.fstat(fd).st_size
        # sysfs attributes advertise PAGE_SIZE, not their actual content size.
        if not root_owned and size > limit:
            raise Refused("input exceeds its size limit")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit or (not root_owned and len(data) != size):
            raise Refused("oversized, truncated or changing input")
        return data
    finally:
        os.close(fd)


def pinned_tools(kernel):
    directory = safe_path(kernel) / "tools/aurora-sep"
    sources = {name: read_input(directory / name, MAX_REPORT) for name in TOOL_HASHES}
    if any(digest(sources[name]) != wanted for name, wanted in TOOL_HASHES.items()):
        raise Refused("pinned kernel factory reader/extractor hash mismatch")
    modules = {}
    # Compile verified bytes, not a subsequently reopened path or a pyc file.
    for name in TOOL_HASHES:
        namespace = {"__name__": "_pinned_sensor_" + name, "__file__": str(directory / name)}
        exec(compile(sources[name], str(directory / name), "exec"), namespace)
        modules[name] = namespace
    return modules["aurora-touchid-setup"], modules["extract-mesa-calibration.py"]


def integer_property(properties, name, expected=None):
    value = properties.get(name)
    if value is None or value[0] != 0x02 or not value[1] or value[1][0] & 0x80:
        raise Refused("missing or malformed integer identity metadata")
    encoded = value[1]
    if len(encoded) > 1 and encoded[0] == 0 and not encoded[1] & 0x80:
        raise Refused("noncanonical integer identity metadata")
    number = int.from_bytes(encoded, "big")
    if expected is not None and number != expected:
        raise Refused("factory metadata identifies another board or chip")
    return number


def strict_manifest(data, factory, extractor):
    """Require bounded DER and real signature/certificate *structure*, not trust."""
    properties = factory["manifest"](data)
    tree = extractor["TLV"](data, 0, allow_high_tag=True)
    if tree.end != len(data):
        raise Refused("trailing manifest data")
    extractor["validate_der_tree"](data, tree, [16384])
    der = factory["DER"](data)
    _, _, start, end = der.whole()
    top = der.children(start, end, 1)
    if data[top[1][3]:top[1][4]] != b"\x00":
        raise Refused("unsupported IM4M version")
    signature = data[top[3][3]:top[3][4]]
    if not 64 <= len(signature) <= 4096:
        raise Refused("missing or malformed signature metadata")
    certificates = der.children(top[4][3], top[4][4], 2)
    if not 1 <= len(certificates) <= 16:
        raise Refused("missing or excessive certificate metadata")
    for certificate in certificates:
        if certificate[1] != 0x30:
            raise Refused("malformed certificate metadata")
        parts = der.children(certificate[3], certificate[4], 3)
        if len(parts) != 3 or [part[1] for part in parts] != [0x30, 0x30, 0x03]:
            raise Refused("unsupported certificate structure")
        algorithm = der.children(parts[1][3], parts[1][4], 4)
        if not algorithm or algorithm[0][1] != 0x06 or algorithm[0][3] == algorithm[0][4]:
            raise Refused("certificate lacks a signature algorithm")
        bits = data[parts[2][3]:parts[2][4]]
        if len(bits) < 65 or bits[0] != 0 or parts[0][3] == parts[0][4]:
            raise Refused("certificate lacks signed content or signature metadata")
    return properties




def current_identity(factory, extractor):
    if os.geteuid() != 0:
        raise Refused("current boot/device binding requires root")
    compatible = read_input(DT / "compatible", 4096, root_owned=True).split(b"\x00")
    if b"apple,j716s" not in compatible or b"apple,t6050" not in compatible:
        raise Refused("the running target is not J716s/T6050")
    evidence = {"firmware_binding": "matched-pinned-label-mboot-profile", "current_restore_build_verified": False}
    for name, iboot_name in (("asahi,os-fw-version", "asahi,iboot2-version"),
                             ("asahi,system-fw-version", "asahi,iboot1-version")):
        raw = read_input(DT / "chosen" / name, 4096, root_owned=True)
        iboot = read_input(DT / "chosen" / iboot_name, 4096, root_owned=True)
        if raw != FIRMWARE_LABEL or iboot != FIRMWARE_IBOOT:
            raise Refused("the current boot's exact OS/system mBoot pairing is not the pinned 26C5035r profile")
        evidence[name], evidence[iboot_name] = digest(raw), digest(iboot)
    policies = []
    # /sys/firmware/devicetree is a real kernel tree, not /proc/device-tree's alias.
    for item in DT.rglob("compatible"):
        raw = read_input(item, 65536, root_owned=True)
        if b"apple,sep" in raw.split(b"\x00"):
            try:
                policies.append(read_input(item.with_name("local-policy-manifest"), MAX_RECORD, root_owned=True))
            except FileNotFoundError:
                pass
    if not policies:
        raise Refused("current kernel lacks forwarded local-policy-manifest bytes; boot the existing SEP discovery candidate without loading SEP before factory preparation")
    if len(policies) != 1:
        raise Refused("the current boot has no unique SEP local policy manifest")
    policy = policies[0]
    properties = strict_manifest(policy, factory, extractor).get(b"MANP", {})
    chip = integer_property(properties, b"CHIP", CHIP)
    ecid = integer_property(properties, b"ECID")
    if b"BORD" in properties:
        integer_property(properties, b"BORD", BOARD)
    if not 0 < ecid < 1 << 64:
        raise Refused("invalid boot ECID metadata")
    serial = read_input(DT / "serial-number", 256, root_owned=True).rstrip(b"\x00")
    if not serial or any(byte < 0x21 or byte > 0x7e for byte in serial):
        raise Refused("the current boot lacks valid unit serial metadata")
    identity = (chip, ecid, serial)
    evidence.update({"local_policy_sha256": digest(policy), "unit_sha256": unit_digest(identity)})
    return identity, evidence


def unit_digest(identity):
    chip, ecid, serial = identity
    return digest(b"J716s-unit-v1\x00" + struct.pack(">IQI", chip, ecid, len(serial)) + serial)


def device_binding(path, partition_uuid):
    if os.geteuid() != 0:
        raise Refused("raw-device binding requires root")
    wanted = str(uuid.UUID(partition_uuid))
    parent, name = open_parent(path, root_owned=True)
    try:
        fd = os.open(name, FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o002:
            raise Refused("source device is not a root-controlled block device")
        sys_device = Path("/sys/dev/block") / f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
        real_sys = sys_device.resolve(strict=True)  # Only the kernel's sysfs link, never user input.
        if not str(real_sys).startswith("/sys/devices/platform/") or "nvme" not in real_sys.parts:
            raise Refused("source is not the current target's internal platform NVMe partition")
        read_input(real_sys / "partition", 128, root_owned=True)
        size = int(read_input(real_sys / "size", 128, root_owned=True)) * 512
        tool = shutil.which("lsblk")
        if tool is None:
            raise Refused("read-only partition metadata tool lsblk is unavailable")
        # Distribution /bin may alias /usr/bin; resolve this trusted tool only.
        tool = Path(tool).resolve(strict=True)
        tool_fd = open_input(tool, root_owned=True)
        os.close(tool_fd)
        result = subprocess.run([str(tool), "--json", "--bytes", "--nodeps", "--output",
                                 "PATH,TYPE,PARTLABEL,PARTUUID,PARTTYPE,SIZE", str(safe_path(path))],
                                capture_output=True, check=False, timeout=30,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        if result.returncode:
            raise Refused("cannot obtain read-only GPT partition metadata")
        entries = json.loads(result.stdout).get("blockdevices", [])
        if len(entries) != 1:
            raise Refused("ambiguous partition metadata")
        entry = entries[0]
        if (entry.get("type") != "part" or entry.get("partlabel") != "iBootSystemContainer"
                or str(entry.get("partuuid", "")).lower() != wanted
                or str(entry.get("parttype", "")).lower() != ISC_TYPE
                or int(entry.get("size", 0)) != size or not 4096 <= size <= MAX_SOURCE):
            raise Refused("device is not the explicitly identified iBootSystemContainer partition")
        # Recheck rdev after the metadata command, through the same no-link opener.
        parent, name = open_parent(path, root_owned=True)
        try:
            again = os.stat(name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)
        if again.st_rdev != info.st_rdev or not stat.S_ISBLK(again.st_mode):
            raise Refused("device identity changed during binding")
        return fd, {"partition_uuid": wanted, "partition_type": ISC_TYPE,
                    "partition_name": "iBootSystemContainer", "size": size,
                    "device_major": os.major(info.st_rdev), "device_minor": os.minor(info.st_rdev)}
    except BaseException:
        os.close(fd)
        raise


def fd_digest(fd, size):
    hasher, offset = hashlib.sha256(), 0
    while offset < size:
        block = os.pread(fd, min(4 * 1024 * 1024, size - offset), offset)
        if not block:
            raise Refused("source shorter than its bound partition size")
        hasher.update(block)
        offset += len(block)
    return hasher.hexdigest()


def read_factory(fd, size, factory):
    def bounded_read(offset, length):
        if offset < 0 or length < 0 or offset > size - length:
            raise Refused("APFS read outside the bound partition")
        data = os.pread(fd, length, offset)
        if len(data) != length:
            raise Refused("short APFS read")
        return data

    container = factory["Container"](bounded_read)
    if container.blocks * container.block_size != size:
        raise Refused("APFS container size does not exactly match its partition")
    records = container.hardware()
    for name in records:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", name) or name in (".", ".."):
            raise Refused("unsafe or unsupported factory cache filename")
    sb = container.superblock
    container_uuid = str(uuid.UUID(bytes=sb[72:88]))
    if container_uuid == str(uuid.UUID(int=0)):
        raise Refused("APFS container UUID is absent")
    return records, {"container_uuid": container_uuid, "checkpoint_xid": factory["xid"](sb),
                     "checkpoint_sha256": digest(sb), "block_size": container.block_size,
                     "hardware_role": factory["APFS_ROLE_HARDWARE"],
                     "factory_path": "/".join(factory["APFS_FACTORY"])}


def validate_container(data, kind, identity, factory, extractor):
    if not 0 < len(data) <= MAX_RECORD:
        raise Refused("empty or oversized factory firmware")
    image, trust = factory["combined"](data)
    if not 0 < len(image) <= 4 * 1024 * 1024:
        raise Refused("embedded IMG4 exceeds the pinned 4MiB source limit")
    payload, seal = factory["image"](image, kind)
    properties = strict_manifest(seal, factory, extractor)
    manp = properties.get(b"MANP", {})
    integer_property(manp, b"BORD", BOARD)
    for field, wanted in ((b"CHIP", identity[0]), (b"ECID", identity[1])):
        if field in manp:
            integer_property(manp, field, wanted)
    if manp.get(b"SrNm") != (0x04, identity[2]):
        raise Refused("firmware seal identifies another unit")
    if properties.get(kind, {}).get(b"DGST") != (0x04, hashlib.sha384(payload).digest()):
        raise Refused("firmware payload does not match its signed digest metadata")
    der = factory["DER"](payload)
    _, _, start, end = der.whole()
    fields = der.children(start, end, 1)
    body = payload[fields[3][3]:fields[3][4]]
    if not body:
        raise Refused("factory payload is empty")
    if kind == b"FSC2" and b"CALB" not in body[:256]:
        raise Refused("FSC2 payload lacks the expected CALB header")
    # MSRk payload and secb are opaque to the pinned consumer. Do not fabricate
    # an MSRK magic/size or claim independent trust-chain verification.
    return {"sha256": digest(data), "size": len(data), "kind": kind.decode(),
            "payload_sha384": hashlib.sha384(payload).hexdigest(),
            "seal_sha256": digest(seal), "trust_sha256": digest(trust)}


def prepare_records(records, identity, factory, extractor):
    if identity[0] != CHIP:
        raise Refused("factory preparation requires T6050")
    seals = [name for name in records if name.lower().startswith("seal-")]
    if len(seals) != 1:
        raise Refused("factory cache must contain exactly one same-unit seal")
    if len(records[seals[0]]) > factory["SOURCE_LIMIT"]:
        raise Refused("factory seal exceeds the pinned 4MiB source limit")
    seal_properties = strict_manifest(records[seals[0]], factory, extractor)
    integer_property(seal_properties.get(b"MANP", {}), b"BORD", BOARD)
    # The upstream seal filename binds CHIP/ECID even if its MANP omits them.
    for field, wanted in ((b"CHIP", identity[0]), (b"ECID", identity[1])):
        if field in seal_properties.get(b"MANP", {}):
            integer_property(seal_properties[b"MANP"], field, wanted)
    source_names = []
    for kind in NAMES.values():
        found = [name for name in records if name.startswith(kind.decode() + "-")]
        if len(found) != 1 or re.fullmatch(kind.decode() + r"-[A-Za-z0-9][A-Za-z0-9_.-]{0,249}", found[0]) is None:
            raise Refused("missing, ambiguous or unsupported FSC2/MSRk factory record naming")
        source_names.append(found[0])
        if len(records[found[0]]) > factory["SOURCE_LIMIT"]:
            raise Refused("factory IMG4 exceeds the pinned 4MiB source limit")
        _, original_seal = factory["image"](records[found[0]], kind)
        strict_manifest(original_seal, factory, extractor)
    if source_names[0].split("-", 1)[1] != source_names[1].split("-", 1)[1]:
        raise Refused("calibration and signed root name different sensors")
    outputs, evidence = {}, {}
    for name, kind in NAMES.items():
        data, original = factory["admit"](records, identity, kind)
        info = validate_container(data, kind, identity, factory, extractor)
        # Private report retains digests, never raw serials, certificates or payloads.
        original.pop("source")
        info["original_record"] = original
        outputs[name], evidence[name] = data, info
    return outputs, evidence


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def write_package(path, outputs, report):
    parent, name = open_parent(path)
    directory = None
    try:
        # Exclusive directory creation prevents merging with another package.
        os.mkdir(name, mode=0o700, dir_fd=parent)
        directory = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
        os.fchmod(directory, 0o700)
        os.mkdir("apple", mode=0o700, dir_fd=directory)
        apple = os.open("apple", FLAGS | os.O_DIRECTORY, dir_fd=directory)
        try:
            for relative, data in outputs.items():
                if relative not in NAMES:
                    raise Refused("unexpected output name")
                write_exclusive(apple, Path(relative).name, data)
            os.fsync(apple)
        finally:
            os.close(apple)
        encoded = json_bytes(report)
        write_exclusive(directory, REPORT, encoded)
        os.fsync(directory)
        os.fsync(parent)
        return digest(encoded)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)
    # A failed write intentionally leaves an incomplete private directory.
    # It is never admitted by check or reused by prepare; no unrelated cleanup.


def write_exclusive(directory, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def prepare(args, factory, extractor):
    identity, boot = current_identity(factory, extractor)
    device, binding = device_binding(args.device, args.partition_uuid)
    source_fd = None
    try:
        size = binding["size"]
        before = fd_digest(device, size)
        if args.source is None:
            if args.source_sha256 is not None:
                raise Refused("--source-sha256 requires a regular partition image")
            source = device
            origin = "current-root-verified-raw-device"
        else:
            wanted = expected_digest(args.source_sha256)
            source_fd = open_input(args.source, private=True)
            if os.fstat(source_fd).st_size != size or fd_digest(source_fd, size) != wanted or wanted != before:
                raise Refused("image does not exactly match its digest and the current same-unit partition")
            source = source_fd
            origin = "private-image-byte-identical-to-current-partition"
        records, apfs = read_factory(source, size, factory)
        outputs, evidence = prepare_records(records, identity, factory, extractor)
        if fd_digest(device, size) != before or (source != device and fd_digest(source, size) != before):
            raise Refused("source partition/image changed during preparation; no output published")
        report = {"schema": 1, "recipe": RECIPE, "model": MODEL, "board": "j716s", "soc": "t6050",
                  "profile_macos_build": BUILD, "kernel_commit": KERNEL_COMMIT, "tool_sha256": TOOL_HASHES,
                  "boot": boot, "source": dict(binding, sha256=before, origin=origin),
                  "apfs": apfs, "files": evidence, "apple_signature_verification": "not-performed",
                  "installation": "not-performed", "sensor_or_sep_actions": "none"}
        anchor = write_package(args.output, outputs, report)
        return {"status": "prepared-private-package", "provenance_sha256": anchor,
                "apple_signature_verification": "not-performed", "installation": "not-performed"}
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(device)


def private_package(directory):
    directory = safe_path(directory)
    for path, expected in ((directory, {"apple", REPORT}), (directory / "apple", {Path(n).name for n in NAMES})):
        parent, name = open_parent(path)
        try:
            fd = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077 or set(os.listdir(fd)) != expected:
                raise Refused("package directory is not private or has unexpected/missing entries")
        finally:
            os.close(fd)
    return directory


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("duplicate provenance JSON property")
        result[key] = value
    return result


def checked_package(directory, provenance_sha256, kernel=None, *, tools=None):
    """Reusable stager/runtime API: return (private file bytes, anchored report).

    Callers must retain provenance_sha256 in their independently root-controlled
    candidate receipt, not read the anchor from this package. No source device,
    source hash traversal, firmware installation or sensor operation occurs.
    """
    factory, extractor = tools if tools is not None else pinned_tools(kernel or PROJECT / "work/sources/kernel")
    directory = private_package(directory)
    raw = read_input(directory / REPORT, MAX_REPORT, private=True)
    if digest(raw) != expected_digest(provenance_sha256):
        raise Refused("provenance report differs from its external SHA256 anchor")
    report = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    for key, wanted in {"schema": 1, "recipe": RECIPE, "model": MODEL, "board": "j716s", "soc": "t6050",
                        "profile_macos_build": BUILD, "kernel_commit": KERNEL_COMMIT, "tool_sha256": TOOL_HASHES,
                        "apple_signature_verification": "not-performed", "installation": "not-performed",
                        "sensor_or_sep_actions": "none"}.items():
        if report.get(key) != wanted:
            raise Refused("provenance target, tooling or safety contract mismatch")
    identity, boot = current_identity(factory, extractor)
    if report.get("boot") != boot:
        raise Refused("package belongs to another unit, build or boot policy")
    source = report["source"]
    expected_digest(source["sha256"])
    if (source.get("partition_name") != "iBootSystemContainer" or source.get("partition_type") != ISC_TYPE
            or str(uuid.UUID(source["partition_uuid"])) != source["partition_uuid"]
            or not 4096 <= source["size"] <= MAX_SOURCE):
        raise Refused("invalid anchored source partition provenance")
    if set(report["files"]) != set(NAMES):
        raise Refused("unexpected firmware file provenance")
    outputs = {}
    for name, kind in NAMES.items():
        data = read_input(directory / name, MAX_RECORD, private=True)
        info = validate_container(data, kind, identity, factory, extractor)
        recorded = report["files"][name]
        if any(recorded.get(key) != value for key, value in info.items()):
            raise Refused("firmware bytes, format or signed metadata differ from anchored provenance")
        original = recorded["original_record"]
        if (original.get("seal_sha256") != info["seal_sha256"]
                or original.get("trust_sha256") != info["trust_sha256"]
                or original.get("payload_sha384") != info["payload_sha384"]):
            raise Refused("firmware metadata differs from its admitted source record")
        outputs[name] = data
    return outputs, report


def check(args, factory, extractor):
    checked_package(args.package, args.provenance_sha256, tools=(factory, extractor))
    return {"status": "checked-private-package", "provenance_sha256": args.provenance_sha256,
            "apple_signature_verification": "not-performed", "installation": "not-performed"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, default=PROJECT / "work/sources/kernel")
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="read-only extraction into a fresh private package")
    prep.add_argument("--source", type=Path, help="optional private raw APFS partition image, never a whole disk/DMG")
    prep.add_argument("--source-sha256", help="mandatory exact digest for --source")
    prep.add_argument("--output", type=Path, required=True, help="fresh directory; its parent must already exist")
    verify = commands.add_parser("check", help="check anchored firmware against this unit/build/policy, without reading ISC")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--provenance-sha256", required=True, help="digest retained separately at preparation time")
    prep.add_argument("--device", type=Path, required=True, help="real /dev NVMe partition node, never a symlink")
    prep.add_argument("--partition-uuid", required=True, help="explicit GPT unique UUID of the current iSC")
    args = parser.parse_args(argv)
    try:
        factory, extractor = pinned_tools(args.kernel_source)
        result = prepare(args, factory, extractor) if args.command == "prepare" else check(args, factory, extractor)
    except Refused as error:
        print("sensor-firmware: refused: " + str(error), file=sys.stderr)
        return 1
    except Exception as error:
        # Emit only known, static parser blockers. Unknown exception strings
        # may contain unit identifiers or raw material and are never printed.
        blockers = {
            "the boot container is not APFS": "source is not a raw APFS partition (whole-disk/DMG/archives are unsupported)",
            "encrypted Hardware volume": "unsupported encrypted APFS Hardware volume",
            "compressed factory record": "unsupported compressed APFS factory record",
            "unsupported APFS checkpoint area": "unsupported APFS checkpoint area",
            "unsupported APFS block size": "unsupported APFS block size",
            "the boot container has no single Hardware volume": "APFS has no unique Hardware-role factory volume",
            "no factory cache on the Hardware volume": "APFS Hardware volume lacks the factory cache",
            "sparse or malformed APFS extent": "unsupported sparse or malformed APFS factory extent",
            "APFS checksum mismatch": "APFS metadata checksum mismatch",
            "not an FSC2 calibration": "factory calibration is not the required FSC2 IMG4 layout",
            "not an MSRk calibration": "signed root is not the required MSRk IMG4 layout",
            "trustobject-current matches no named trust bundle": "factory trust bundle digest/name mismatch",
            "the trust bundle does not match its name": "factory trust bundle digest/name mismatch",
        }
        reason = blockers.get(str(error), "unsupported/malformed input, unavailable current binding, or I/O failure")
        print("sensor-firmware: refused: " + reason, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
