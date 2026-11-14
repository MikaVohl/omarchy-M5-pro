#!/usr/bin/env python3
"""Compose a J716s boot= bundle using Aura's live-ANS Linux handoff.

Only workspace files are written; installing the bundle requires a separate step.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib
import uuid


def sha(data):
    return hashlib.sha256(data).hexdigest()


def single_gzip(data):
    assert data[:2] == b"\x1f\x8b", "expected gzip"
    stream = zlib.decompressobj(31)
    decoded = stream.decompress(data) + stream.flush()
    assert stream.eof and not stream.unused_data and not stream.unconsumed_tail, "not one gzip member"
    return decoded


def dtb_nodes(data):
    assert len(data) >= 40
    magic, size, tree, strings, reserve, version, last, _, stringsize, treesize = struct.unpack_from(">10I", data)
    assert magic == 0xd00dfeed and size == len(data) and version == 17 and last <= 17
    assert 40 <= reserve <= size-16 and 40 <= tree <= size-treesize
    assert 40 <= strings <= size-stringsize
    result, stack = {}, []
    p = tree
    while p < tree + treesize:
        token = struct.unpack_from(">I", data, p)[0]
        p += 4
        if token == 1:
            end = data.index(0, p, tree+treesize)
            stack.append(data[p:end].decode())
            p = (end + 4) & ~3
            result["/" + "/".join(stack[1:])] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from(">II", data, p)
            p += 8
            assert nameoff < stringsize and p + length <= tree+treesize
            end = data.index(0, strings+nameoff, strings+stringsize)
            name = data[strings+nameoff:end].decode()
            result["/" + "/".join(stack[1:])][name] = data[p:p+length]
            p = (p + length + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            assert not stack
            return result
        else:
            raise ValueError("invalid FDT token")
    raise ValueError("no FDT_END")


def boot_payload(aura, esp_partuuid, files, variables):
    esp_partuuid = str(uuid.UUID(esp_partuuid))
    assert aura.endswith(b"STACKBOT") and b"AURORA-S1-CFG01\0" not in aura
    assert len(files) == 3
    paths = [path for path, _ in files]
    assert len(set(paths)) == 3
    for path in paths:
        assert path.isascii() and not path.startswith("/") and all(
            part not in ("", ".", "..") for part in path.split("/"))
        assert not any(c in path for c in ";\n\r\0")
    spec = "boot=" + esp_partuuid + ";" + ";".join(paths) + "\n"
    assert len(spec.encode()) < 1024
    encoded = spec.encode("ascii") + variables
    assert encoded.endswith(b"\n") and b"\0" not in encoded
    result = aura + encoded
    entries = [{"path": path, "size": len(data), "sha256": sha(data)} for path, data in files]
    return result, encoded, entries


PROJECT = Path(__file__).resolve().parents[2]
BOOT = PROJECT / "outputs/boot-candidate"
KIS_ADDRESSES = {0x2a2280000, 0x2a2f00000, 0x2a2f80000,
                 0x382280000, 0x382f00000, 0x382f80000}
BOOTARGS = ("rdinit=/init console=tty0 console=ttyDC0 panic=0 pd_ignore_unused "
            "clk_ignore_unused no_console_suspend nvme_apple.flush_interval=0 "
            "arm64.nowfxt kvm-arm.wfi_trap_policy=trap earlycon=dockchannel,0x288e3c000 "
            "loglevel=8 ignore_loglevel initcall_debug arm64.nomte appledrm.show_notch=1")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_file(relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Unsafe project-relative artifact")
    result = PROJECT / path
    require(result.resolve().is_relative_to(PROJECT.resolve()) and result.is_file() and not result.is_symlink(),
            "Missing or escaped artifact: " + str(path))
    return result


def checked_artifact(directory, filename, record):
    path = project_file(str((directory / filename).relative_to(PROJECT)))
    data = path.read_bytes()
    require(record.get("sha256") == sha(data) and record.get("bytes") == len(data),
            "Artifact differs from fresh build manifest: " + filename)
    return data


def build_manifests():
    profile = json.loads((PROJECT / "profiles/j716s.json").read_text())
    require((profile["model"], profile["board"], profile["macos_build"]) == ("Mac17,8", "j716s", "26C5035r"),
            "Wrong source identity")
    kernel_path = PROJECT / "outputs/kernel-candidate/build-manifest.json"
    loader_path = BOOT / "loader/build-manifest.json"
    kernel, loader = json.loads(kernel_path.read_text()), json.loads(loader_path.read_text())
    require(kernel.get("schema") == 1 and kernel.get("commit") == profile["sources"]["kernel"]["commit"]
            and kernel.get("kernel_release") == profile["kernel_release"], "Wrong kernel build identity")
    require(loader.get("schema") == 1 and loader.get("base_commit") == profile["sources"]["aura"]["commit"],
            "Wrong loader build identity")
    require([patch["path"] for patch in loader.get("patches", [])] == [
        "work/aura-audit/j716s-26C5035r-integration.patch", "patches/aura-j716s-fixed-clocks.patch",
        "patches/aura-j716s-kis-debug.patch"], "Loader lacks exact qualified KIS/fixed-clock patch recipe")
    for patch in loader["patches"]:
        require(sha(project_file(patch["path"]).read_bytes()) == patch["sha256"], "Loader patch changed")
    # Validate both stages, not an arbitrary binary placed at the Stage 2 path.
    for stage in ("stage1", "stage2"):
        records = loader["stages"][stage]
        require("aura.bin" in records, "Loader manifest lacks " + stage + " aura.bin")
        for filename, record in records.items():
            checked_artifact(BOOT / "loader" / stage, filename, record)
    return profile, kernel, loader, sha(kernel_path.read_bytes()), sha(loader_path.read_bytes())


def kis_targets(nodes):
    result = {}
    for path, properties in nodes.items():
        reg = properties.get("reg", b"")
        if len(reg) < 8:
            continue
        address = struct.unpack_from(">Q", reg)[0]
        if address not in KIS_ADDRESSES:
            continue
        compatible = properties.get("compatible", b"").split(b"\0")
        wanted = b"apple,t6050-dart" if address in (0x2a2f00000, 0x2a2f80000, 0x382f00000, 0x382f80000) else (
            b"snps,dwc3" if address == 0x2a2280000 else b"apple,t6050-dwc3")
        require(wanted in compatible, "KIS topology compatible mismatch: " + path)
        result[path] = address
    require(len(result) == 6 and set(result.values()) == KIS_ADDRESSES, "Missing USB0/USB2 KIS reservation topology")
    return result


def prepare_dtb(fdtput="fdtput"):
    _, kernel, _, kernel_sha, _ = build_manifests()
    original = checked_artifact(PROJECT / "outputs/kernel-candidate", "t6050-j716s.dtb",
                                kernel["artifacts"]["t6050-j716s.dtb"])
    before = dtb_nodes(original)
    targets = kis_targets(before)
    output = BOOT / "t6050-j716s-kis02.dtb"
    receipt = output.with_suffix(output.suffix + ".json")
    require(not output.exists() and not receipt.exists() and not output.is_symlink() and not receipt.is_symlink(),
            "Refusing existing qualified-source DTB")
    with tempfile.TemporaryDirectory(prefix="j716s-kis-dtb-") as temporary:
        path = Path(temporary) / "input.dtb"
        path.write_bytes(original)
        for node in targets:
            require(before[node].get("status") in (b"okay\0", b"disabled\0"), "Unexpected KIS node status")
            if before[node].get("status") != b"disabled\0":
                subprocess.run([fdtput, "-t", "s", str(path), node, "status", "disabled"], check=True)
        result = path.read_bytes()
    after = dtb_nodes(result)
    require(before.keys() == after.keys(), "DTB topology changed")
    changes = []
    for node in before:
        expected = dict(before[node])
        if node in targets:
            expected["status"] = b"disabled\0"
            if before[node].get("status") != expected["status"]:
                changes.append({"path": node, "address": hex(targets[node]), "property": "status",
                                "before": "okay", "after": "disabled"})
        require(after[node] == expected, "Unexpected DTB semantic change: " + node)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(result)
    report = {"schema": 1, "sha256": sha(result), "bytes": len(result), "source_sha256": sha(original),
              "kernel_build_manifest_sha256": kernel_sha, "dtb_semantic_changes": changes,
              "kis_reserved_addresses": [hex(value) for value in sorted(KIS_ADDRESSES)]}
    with receipt.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    return report


def compose(esp_partuuid, output=None):
    require(__debug__, "Run without -O: FDT/payload helpers require assertion checks")
    esp_partuuid = str(uuid.UUID(esp_partuuid))
    require(uuid.UUID(esp_partuuid).int != 0, "Zero is not a target ESP PARTUUID")
    profile, kernel_manifest, loader_manifest, kernel_manifest_sha, loader_manifest_sha = build_manifests()
    output = output or BOOT / "j716s-rescue-qualified-kis.bin"
    receipt = output.with_suffix(output.suffix + ".json")
    require(not output.exists() and not receipt.exists() and not output.is_symlink() and not receipt.is_symlink(),
            "Refusing existing rescue output")
    aura = checked_artifact(BOOT / "loader/stage2", "aura.bin", loader_manifest["stages"]["stage2"]["aura.bin"])
    require(aura.endswith(b"STACKBOT") and b"AURORA-S1-CFG01\0" not in aura, "Not bare ESP Stage 2")
    require(b"J716s KIS diagnostic: in-memory aura-debug enabled" in aura, "KIS loader source recipe not linked")
    kernel = checked_artifact(PROJECT / "outputs/kernel-candidate", "Image.gz", kernel_manifest["artifacts"]["Image.gz"])
    dtb_path = project_file("outputs/boot-candidate/t6050-j716s-kis02.dtb")
    dtb = dtb_path.read_bytes()
    dtb_receipt = json.loads(dtb_path.with_suffix(".dtb.json").read_text())
    require(dtb_receipt.get("schema") == 1 and dtb_receipt.get("sha256") == sha(dtb)
            and dtb_receipt.get("bytes") == len(dtb) and dtb_receipt.get("kernel_build_manifest_sha256") == kernel_manifest_sha,
            "Qualified-source DTB provenance differs")
    require(dtb_receipt.get("source_sha256") == kernel_manifest["artifacts"]["t6050-j716s.dtb"]["sha256"],
            "DTB is not derived from the fresh pinned kernel build")
    initramfs_path = project_file("outputs/boot-candidate/j716s-rescue-initramfs.cpio.gz")
    initramfs = initramfs_path.read_bytes()
    init_receipt = json.loads(initramfs_path.with_suffix(".gz.json").read_text())
    require(init_receipt.get("schema") == 1 and init_receipt.get("kind") == "j716s-rescue-initramfs"
            and init_receipt.get("sha256") == sha(initramfs) and init_receipt.get("bytes") == len(initramfs)
            and init_receipt.get("root_selection") == "RAM only", "Wrong rescue initramfs receipt")
    require(init_receipt.get("init_sha256") == sha((BOOT / "rescue-init.sh").read_bytes()), "Rescue init script changed")
    image, cpio = single_gzip(kernel), single_gzip(initramfs)
    require(len(image) >= 64 and image[0x38:0x3c] == b"ARM\x64", "Not an arm64 Image")
    text_offset, image_size = struct.unpack_from("<QQ", image, 8)
    require(len(image) <= image_size and image_size and text_offset + image_size <= 128*1024*1024,
            "Kernel does not fit Aura's CTXR execute window")
    require(cpio[:6] in (b"070701", b"070702"), "Not a newc initramfs")
    nodes = dtb_nodes(dtb)
    compatible = nodes["/"]["compatible"].rstrip(b"\0").decode().split("\0")
    require("apple,j716s" in compatible and "apple,t6050" in compatible, "Wrong board DTB")
    for node in kis_targets(nodes):
        require(nodes[node].get("status") == b"disabled\0", "KIS USB0/2 reservation lost")
    original = checked_artifact(PROJECT / "outputs/kernel-candidate", "t6050-j716s.dtb",
                                kernel_manifest["artifacts"]["t6050-j716s.dtb"])
    original_nodes = dtb_nodes(original)
    require(original_nodes.keys() == nodes.keys(), "Qualified-source DTB topology changed")
    original_targets = kis_targets(original_nodes)
    for node, properties in original_nodes.items():
        expected = dict(properties)
        if node in original_targets:
            expected["status"] = b"disabled\0"
        require(nodes[node] == expected, "Qualified-source DTB has unrelated semantic changes: " + node)
    disabled = {}
    for alias in ("gpu", "dcp", "disp0"):
        node = nodes["/aliases"][alias].rstrip(b"\0").decode()
        require(nodes[node].get("status") == b"disabled\0", alias + " must start disabled; Aura opt-ins own handoff")
        disabled[alias] = node
    variables = ("chosen.stdout-path=serial0\nchosen.m5,t6050-display=1\nchosen.m5pro,gpu=1\n"
                 "chosen.asahi,show-notch=1\nchosen.bootargs=" + BOOTARGS + "\n").encode()
    files = [("aurora/rescue/Image.gz", kernel), ("aurora/rescue/t6050-j716s.dtb", dtb),
             ("aurora/rescue/initramfs.cpio.gz", initramfs)]
    result, encoded, external = boot_payload(aura, esp_partuuid, files, variables)
    sources = ("outputs/kernel-candidate/Image.gz", "outputs/boot-candidate/t6050-j716s-kis02.dtb",
               "outputs/boot-candidate/j716s-rescue-initramfs.cpio.gz")
    for entry, source in zip(external, sources):
        entry["source"] = source
    report = {"schema": 1, "kind": "j716s-qualified-source-kis-dcp-gpu-ram-recovery",
              "status": "rebuilt bytes require physical qualification before persistent deployment",
              "hardware_qualified": False, "esp_partuuid": str(uuid.UUID(esp_partuuid)),
              "sha256": sha(result), "size": len(result), "files": external,
              "bare_sha256": sha(aura), "bare_size": len(aura), "variables_sha256": sha(encoded),
              "aura_base": profile["sources"]["aura"]["commit"], "kernel_commit": profile["sources"]["kernel"]["commit"],
              "kernel_build_manifest_sha256": kernel_manifest_sha, "loader_build_manifest_sha256": loader_manifest_sha,
              "dtb_source_sha256": dtb_receipt["source_sha256"], "dtb_semantic_changes": dtb_receipt["dtb_semantic_changes"],
              "native_gpu_dcp_initially_disabled": disabled, "dcp_requested": True, "gpu_requested": True,
              "mte_explicitly_disabled": True, "kis_reserved": True, "bootargs": BOOTARGS,
              "rootfs_written": False, "storage_handoff": "live ANS/NVMe adoption; init never mounts a block device",
              "parts": [{"kind": "aura", "offset": 0, "size": len(aura), "sha256": sha(aura)},
                        {"kind": "variables", "offset": len(aura), "size": len(encoded), "sha256": sha(encoded)}]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(result)
    with receipt.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esp-partuuid", required=True, help="actual discovered target ESP PARTUUID")
    parser.add_argument("--output", type=Path, default=BOOT / "j716s-rescue-qualified-kis.bin")
    args = parser.parse_args()
    print(json.dumps(compose(args.esp_partuuid, args.output), indent=2))


if __name__ == "__main__":
    main()
