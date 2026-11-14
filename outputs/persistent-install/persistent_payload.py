#!/usr/bin/env python3
"""Compose the exact J716s persistent payload only when real GPT identities exist."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
KERNEL_RELEASE = "7.3.0-j716s-8ab10100a4c1"
FIRMWARE_BUILD = "26C5035r"
KERNEL_COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
AURA_COMMIT = "54678354b70d64506408d9b157f85ac6065d194b"
KERNEL_FILES = {"Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map", "Module.symvers", "kernel.release", "modules.tar.gz"}
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_uuid(value):
    require(isinstance(value, str) and UUID_RE.fullmatch(value), "Missing or malformed UUID")
    return str(uuid.UUID(value))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def project_source(value):
    relative = Path(value)
    require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe project source path")
    path = PROJECT / relative
    require(path.resolve() == path and path.is_file() and not path.is_symlink(), "Missing or unsafe project source: " + value)
    return path


def verify_artifact(path, record):
    require(path.is_file() and not path.is_symlink(), "Artifact missing or symlinked: " + str(path))
    require(path.stat().st_size == record["bytes"] and digest(path) == record["sha256"], "Build artifact differs: " + str(path))


def kernel_build(kernel_dir):
    kernel_dir = Path(kernel_dir)
    path = kernel_dir / "build-manifest.json"
    record = json.loads(path.read_text())
    require(record.get("schema") == 1 and record.get("commit") == KERNEL_COMMIT
            and record.get("kernel_release") == KERNEL_RELEASE, "Kernel build source pin differs")
    require(set(record.get("artifacts", {})) == KERNEL_FILES, "Kernel artifact inventory differs")
    for name, artifact in record["artifacts"].items():
        verify_artifact(kernel_dir / name, artifact)
    require((kernel_dir / "kernel.release").read_text().strip() == KERNEL_RELEASE, "Kernel release file differs")
    return record, digest(path)


def loader_build():
    directory = PROJECT / "outputs/boot-candidate/loader"
    path = directory / "build-manifest.json"
    record = json.loads(path.read_text())
    require(record.get("schema") == 1 and record.get("base_commit") == AURA_COMMIT, "Aura build source pin differs")
    patches = record.get("patches", [])
    require([item["path"] for item in patches] == ["work/aura-audit/j716s-26C5035r-integration.patch",
            "patches/aura-j716s-fixed-clocks.patch", "patches/aura-j716s-kis-debug.patch"], "Aura patch inventory differs")
    for item in patches:
        require(digest(project_source(item["path"])) == item["sha256"], "Aura source patch differs")
    require(set(record.get("stages", {})) == {"stage1", "stage2"}, "Aura stage inventory differs")
    for stage, files in record["stages"].items():
        require("aura.bin" in files, "Aura stage binary missing")
        for name, artifact in files.items():
            require(Path(name).name == name and name not in (".", ".."), "Unsafe Aura stage filename")
            verify_artifact(directory / stage / name, artifact)
    return record, digest(path)


def rescue_manifest(kernel_dir, esp_uuid=None):
    kernel, kernel_hash = kernel_build(kernel_dir)
    loader, loader_hash = loader_build()
    rescue = PROJECT / "outputs/boot-candidate/j716s-rescue-qualified-kis.bin"
    path = rescue.with_suffix(".bin.json")
    record = json.loads(path.read_text())
    require(record.get("schema") == 1 and record.get("kernel_commit") == KERNEL_COMMIT
            and record.get("aura_base") == AURA_COMMIT, "Rescue source pins differ")
    require(record.get("kernel_build_manifest_sha256") == kernel_hash
            and record.get("loader_build_manifest_sha256") == loader_hash, "Rescue build crossbinding differs")
    require(record.get("bare_sha256") == loader["stages"]["stage2"]["aura.bin"]["sha256"], "Rescue Aura differs")
    require(record.get("kis_reserved") is True and record.get("dcp_requested") is True
            and record.get("gpu_requested") is True and record.get("mte_explicitly_disabled") is True,
            "Rescue is not the qualified KIS/GPU/DCP recipe")
    verify_artifact(rescue, {"sha256": record["sha256"], "bytes": record["size"]})
    expected = {"aurora/rescue/Image.gz": "outputs/kernel-candidate/Image.gz",
                "aurora/rescue/t6050-j716s.dtb": "outputs/boot-candidate/t6050-j716s-kis02.dtb",
                "aurora/rescue/initramfs.cpio.gz": "outputs/boot-candidate/j716s-rescue-initramfs.cpio.gz"}
    files = record.get("files", [])
    require(len(files) == 3 and {item["path"]: item["source"] for item in files} == expected,
            "Rescue dependency inventory differs")
    for item in files:
        verify_artifact(project_source(item["source"]), {"sha256": item["sha256"], "bytes": item["size"]})
    image = next(item for item in files if item["path"].endswith("/Image.gz"))
    require(image["sha256"] == kernel["artifacts"]["Image.gz"]["sha256"], "Rescue kernel differs")
    if esp_uuid is not None:
        require(canonical_uuid(record["esp_partuuid"]) == canonical_uuid(esp_uuid), "Rescue ESP binding differs")
    return record, digest(path)


def root_manifest(path, kernel_dir=None):
    kernel_dir = Path(kernel_dir) if kernel_dir is not None else PROJECT / "outputs/kernel-candidate"
    kernel, kernel_hash = kernel_build(kernel_dir)
    _, loader_hash = loader_build()
    manifest = json.loads(Path(path).read_text())
    require(manifest.get("schema") == 1 and manifest.get("kind") == "j716s-omarchy-rootfs", "Wrong root manifest schema")
    require(manifest.get("board") == "j716s" and manifest.get("filesystem") == "ext4", "Root image board/filesystem differs")
    canonical_uuid(manifest.get("filesystem_uuid"))
    require(manifest.get("block_size") == 4096, "Root image must use 4KiB ext4 blocks")
    require(manifest.get("kernel_release") == KERNEL_RELEASE and manifest.get("firmware_build") == FIRMWARE_BUILD,
            "Root image kernel or firmware differs from pinned source profile")
    require(manifest.get("kernel_build_manifest_sha256") == kernel_hash
            and manifest.get("loader_build_manifest_sha256") == loader_hash, "Root image build manifests differ")
    for name in ("j716s-bind-boot.py", "candidate_boot.py", "j716s-firstboot.sh", "m5-omarchy-j716s.sh", "policies/j716s-policy.py"):
        require(manifest.get("provisioning_policies", {}).get(name, {}).get("sha256") == digest(HERE / name),
                "Root image runtime guard is stale: " + name)
    for name, expected in kernel["artifacts"].items():
        require(manifest.get("kernel_artifacts", {}).get(name) == {"sha256": expected["sha256"], "size_bytes": expected["bytes"]},
                "Root image kernel artifact differs: " + name)
    identity = manifest.get("image_identity", {})
    require(identity.get("kernel_build_manifest_sha256") == kernel_hash
            and identity.get("loader_build_manifest_sha256") == loader_hash, "Embedded image build crossbinding differs")
    helpers = {"usr/local/bin/j716s-gpu-preflight": "j716s-gpu-preflight.py",
               "usr/local/libexec/j716s-session-env": "j716s-session-env.py"}
    require(set(manifest.get("graphics_helpers", {})) == set(helpers)
            and manifest["graphics_helpers"] == identity.get("graphics_helpers"), "Image graphics helper inventory differs")
    for installed, source in helpers.items():
        path = HERE / "graphics" / source
        require(manifest["graphics_helpers"][installed] == {"sha256": digest(path), "size_bytes": path.stat().st_size},
                "Root image graphics integration is stale: " + source)
    require(hashlib.sha256((json.dumps(identity, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
            == manifest.get("image_identity_sha256"), "Embedded image identity content differs")
    for key in ("sha256", "uncompressed_sha256", "image_identity_sha256"):
        require(isinstance(manifest.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", manifest[key]), "Missing root hash: " + key)
    require(isinstance(manifest.get("image_size"), int) and 0 < manifest["image_size"] <= 150_000_000_000
            and manifest["image_size"] % 4096 == 0, "Invalid root image logical size")
    return manifest


def compose(kernel_dir, initramfs, manifest, linux_uuid, esp_uuid):
    linux_uuid, esp_uuid = canonical_uuid(linux_uuid), canonical_uuid(esp_uuid)
    require(linux_uuid != esp_uuid, "Linux and ESP partition GUIDs collide")
    kernel_record, kernel_hash = kernel_build(kernel_dir)
    loader_record, loader_hash = loader_build()
    rescue, rescue_hash = rescue_manifest(kernel_dir, esp_uuid)
    require(manifest["kernel_build_manifest_sha256"] == kernel_hash
            and manifest["loader_build_manifest_sha256"] == loader_hash, "Root/payload build crossbinding differs")
    bare = PROJECT / "outputs/boot-candidate/loader/stage2/aura.bin"
    initramfs = Path(initramfs)
    init_receipt = json.loads(initramfs.with_suffix(initramfs.suffix + ".json").read_text())
    require(init_receipt.get("kind") == "j716s-persistent-root-initramfs" and init_receipt.get("board") == "j716s"
            and init_receipt.get("firmware_build") == FIRMWARE_BUILD and digest(initramfs) == init_receipt.get("sha256"),
            "Persistent initramfs/firmware receipt mismatch")
    require(init_receipt.get("init_sha256") == digest(HERE / "root-init.sh"), "Init changed since archive preparation")
    required_firmware = {"apple/tpmtfw-j716s.bin", "apple/centauri/j716s-ftab.bin"}
    require(set(init_receipt.get("firmware_sha256", {})) == required_firmware, "Initramfs firmware identities incomplete")
    for name, expected in init_receipt["firmware_sha256"].items():
        require(manifest.get("firmware", {}).get(name, {}).get("sha256") == expected,
                "Root/initramfs firmware differs: " + name)
    helpers_path = PROJECT / "work/aura-audit/compose-linux-rescue.py"
    spec = importlib.util.spec_from_file_location("aura_payload_helpers", helpers_path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    aura = bare.read_bytes()
    dtb_source = PROJECT / "outputs/boot-candidate/t6050-j716s-kis02.dtb"
    dtb_entry = next(item for item in rescue["files"] if item["source"].endswith(".dtb"))
    require(digest(dtb_source) == dtb_entry["sha256"], "Qualified KIS-exclusive DTB differs")
    dtb = dtb_source.read_bytes()
    kernel = Path(kernel_dir, "Image.gz").read_bytes()
    ramfs = initramfs.read_bytes()
    image = helpers.single_gzip(kernel)
    cpio = helpers.single_gzip(ramfs)
    require(aura.endswith(b"STACKBOT") and b"AURORA-S1-CFG01\0" not in aura, "Not bare ESP Aura")
    require(image[0x38:0x3c] == b"ARM\x64" and cpio[:6] in (b"070701", b"070702"), "Wrong kernel/initramfs format")
    text_offset, image_size = struct.unpack_from("<QQ", image, 8)
    require(len(image) <= image_size and image_size > 0 and text_offset + image_size <= 128 * 1024**2,
            "Kernel exceeds the enrolled execute window")
    nodes = helpers.dtb_nodes(dtb)
    require(b"apple,j716s" in nodes["/"]["compatible"].split(b"\0"), "Wrong board device tree")
    for alias in ("gpu", "dcp", "disp0"):
        path = nodes["/aliases"][alias].rstrip(b"\0").decode()
        require(nodes[path].get("status") == b"disabled\0", "Native node must remain loader-gated: " + alias)
    fsuuid = canonical_uuid(manifest["filesystem_uuid"])
    bootargs = (f"rdinit=/init console=ttyDC0 root=PARTUUID={linux_uuid} ro "
                f"j716s.esp-partuuid={esp_uuid} j716s.root-fsuuid={fsuuid} "
                "panic=0 pd_ignore_unused clk_ignore_unused no_console_suspend "
                "nvme_apple.flush_interval=0 arm64.nowfxt kvm-arm.wfi_trap_policy=trap "
                "arm64.nomte earlycon=dockchannel,0x288e3c000 plymouth.enable=0 appledrm.show_notch=1")
    require(len(bootargs.encode("ascii")) < 1000, "Bootargs exceed Aura variable limit")
    known_bare = {"ro", "pd_ignore_unused", "clk_ignore_unused", "no_console_suspend"}
    require(all("=" in token or "." in token or token in known_bare for token in bootargs.split()),
            "Unknown bare kernel argument would reach PID 1")
    variables = ("chosen.stdout-path=serial0\nchosen.bootargs=" + bootargs + "\n"
                 "chosen.asahi,show-notch=1\nchosen.m5pro,gpu=1\nchosen.m5,t6050-display=1\n")
    encoded = variables.encode("ascii")
    require(b"\0" not in encoded, "Payload variables contain an early terminator")
    sources = (Path(kernel_dir, "Image.gz"), dtb_source, initramfs)
    files = [("aurora/root/Image.gz", kernel), ("aurora/root/t6050-j716s.dtb", dtb),
             ("aurora/root/initramfs.cpio.gz", ramfs)]
    result, _, dependencies = helpers.boot_payload(aura, esp_uuid, files, encoded)
    for entry, source in zip(dependencies, sources):
        entry["source"] = str(source.resolve().relative_to(PROJECT))
    parts = [{"kind": "aura", "offset": 0, "bytes": len(aura), "sha256": hashlib.sha256(aura).hexdigest()}]
    receipt = {"schema": 1, "kind": "j716s-persistent-root-payload", "board": "j716s",
               "aura_commit": AURA_COMMIT, "kernel_commit": KERNEL_COMMIT, "kernel_release": KERNEL_RELEASE,
               "firmware_build": FIRMWARE_BUILD, "linux_partition_uuid": linux_uuid, "esp_partition_uuid": esp_uuid,
               "filesystem_uuid": fsuuid, "graphics_profile": "native", "bootargs": bootargs,
               "kernel_build_manifest_sha256": kernel_hash, "loader_build_manifest_sha256": loader_hash,
               "rescue_manifest_sha256": rescue_hash,
               "parts": parts, "files": dependencies, "bytes": len(result), "sha256": hashlib.sha256(result).hexdigest(),
               "status": "configured candidate; physical boot and graphics not established",
               "storage_handoff": "boot= retains validated ANS/NVMe and RTKit for Linux adoption",
               "enrollment": "uses configured raw Stage 1; stub-only Kernel CTRR must be disabled"}
    return bytes(result), receipt
