#!/usr/bin/env python3
"""Validate returned compile artifacts and finalize a candidate locally, never install it."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tarfile
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA256 = "06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2"
CONFIG_SHA256 = {
    "bringup": "e7ff6452a937de895bf351f8539636cbf98998493835b80113da5d5ca4ecba14",
    "baseline": "74a2fef4c7c36ec82cf3dd3849bc7a672f56823b48f1ef29fa1e359abdc2dd47",
}
ARTIFACTS = ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map",
             "Module.symvers", "kernel.release", "modules.tar.gz")
METADATA = ("build-manifest.json", "build-provenance.json", "SHA256SUMS")
IMAGE_LIMIT = 128 * 1024**2
CHUNK = 1024**2
SIGNATURE_MAGIC = b"~Module signature appended~\n"
BUILD_IDENTITY = {"user": "codex", "host": "j716s-kernel-build",
                  "timestamp": "Thu Nov 12 00:00:00 UTC 2026", "version": 1}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regular(path):
    require(path.absolute() == path.resolve() and stat.S_ISREG(path.lstat().st_mode),
            "expected a canonical regular file: " + str(path))
    return path


def record(path):
    regular(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest, "bytes": path.stat().st_size}


def validate_record(value, label):
    require(isinstance(value, dict) and set(value) == {"sha256", "bytes"}
            and isinstance(value["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
            and type(value["bytes"]) is int and value["bytes"] > 0,
            "invalid artifact record: " + label)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, "duplicate receipt key: " + name)
        result[name] = value
    return result


def validate_target_sysroot(value, builder_arch):
    if builder_arch == "aarch64":
        require(value is None, "native builder must not use a target sysroot")
        return
    flags = "--sysroot=/opt/arm64-sysroot --gcc-toolchain=/opt/arm64-sysroot/usr -fuse-ld=lld"
    keys = {"path", "distribution", "version", "arch", "packages", "usercflags", "userldflags", "signed_rpms"}
    require(isinstance(value, dict) and set(value) == keys, "target sysroot schema differs")
    require(value["path"] == "/opt/arm64-sysroot" and value["distribution"] == "Fedora"
            and value["version"] == "43" and value["arch"] == "aarch64",
            "target sysroot identity/architecture differs")
    require(value["usercflags"] == flags and value["userldflags"] == flags,
            "target sysroot compiler flags differ")
    require(value["signed_rpms"] is True, "target sysroot RPM signatures are unverified")
    packages = value["packages"]
    require(isinstance(packages, list) and packages, "target sysroot package inventory is missing")
    identities = []
    for package in packages:
        require(isinstance(package, dict)
                and set(package) == {"name", "epoch", "version", "release", "arch", "sha256", "bytes", "signature_verified"},
                "target sysroot package identity is missing or invalid")
        require(isinstance(package["name"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", package["name"]),
                "target sysroot package name is invalid")
        require(isinstance(package["arch"], str) and package["arch"] in {"aarch64", "noarch"},
                "target sysroot package architecture differs")
        identity = (package["name"], package["arch"])
        require(identity not in identities, "target sysroot package identity is duplicated")
        identities.append(identity)
        require(type(package["epoch"]) is int and package["epoch"] >= 0,
                "target sysroot package epoch identity differs")
        require(all(isinstance(package[key], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~^-]*", package[key])
                    for key in ("version", "release")), "target sysroot package version/release identity differs")
        validate_record({"sha256": package["sha256"], "bytes": package["bytes"]}, "target sysroot RPM")
        require(package["signature_verified"] is True, "target sysroot package signature is unverified")
    require(identities == sorted(identities), "target sysroot package identity order differs")
    require({("gcc", "aarch64"), ("glibc-devel", "aarch64"), ("libgcc", "aarch64")} <= set(identities),
            "target sysroot required AArch64 package identity is missing")


def validate_receipt(receipt, variant, builder_arch, candidate):
    expected_keys = {"schema", "commit", "variant", "qualification", "kernel_release",
                     "artifacts", "patches", "ordered_patches", "archive_sha256",
                     "baseline_config_sha256", "config", "host", "target", "toolchain",
                     "worker_sha256", "input_signature", "signing", "build_identity", "target_sysroot"}
    require(isinstance(receipt, dict) and set(receipt) == expected_keys
            and type(receipt["schema"]) is int and receipt["schema"] == 1,
            "compile receipt schema differs")
    pins = candidate.VARIANTS[variant]
    require(receipt["commit"] == candidate.KERNEL_COMMIT
            and receipt["archive_sha256"] == ARCHIVE_SHA256
            and receipt["variant"] == variant
            and receipt["qualification"] == "unqualified-experimental"
            and receipt["kernel_release"] == pins["release"], "compile source/variant pin differs")
    config_kind = "bringup" if variant in candidate.TOUCHID_VARIANTS else "baseline"
    config_path = "outputs/kernel-" + ("bringup" if config_kind == "bringup" else "open-wifi") + "-candidate/.config"
    require(receipt["config"] == config_path
            and receipt["baseline_config_sha256"] == CONFIG_SHA256[config_kind],
            "compile config pin differs")
    require(receipt["patches"] == pins["patches"]
            and receipt["ordered_patches"] == [{"name": name, "sha256": digest}
                                               for name, digest in pins["patches"].items()],
            "compile patch set/order differs")
    for name, digest in pins["patches"].items():
        require(record(ROOT / "patches" / name)["sha256"] == digest,
                "local patch differs from runtime pin: " + name)
    require(receipt["host"] == {"arch": builder_arch, "distribution": "Fedora", "version": "43"}
            and receipt["target"] == {"arch": "arm64"}
            and receipt["toolchain"] == {"clang": "21.1.8", "rust": "1.99.1"},
            "compile host/target/toolchain differs")
    validate_target_sysroot(receipt["target_sysroot"], builder_arch)
    require(receipt["build_identity"] == BUILD_IDENTITY
            and type(receipt["build_identity"]["version"]) is int, "compile build identity differs")
    require(receipt["worker_sha256"] == record(ROOT / "scripts/kernel-build-worker.sh")["sha256"],
            "compile worker recipe differs")
    require(isinstance(receipt["artifacts"], dict)
            and set(receipt["artifacts"]) == set(ARTIFACTS) == candidate.BUILD_ARTIFACTS,
            "compile artifact inventory differs")
    for name, item in receipt["artifacts"].items():
        validate_record(item, name)
    require(receipt["artifacts"]["Image"]["bytes"] <= IMAGE_LIMIT,
            "recorded Image exceeds the enrolled kernel window")
    require(receipt["artifacts"][".config"]["sha256"] == CONFIG_SHA256[config_kind],
            "compiled config differs from pinned input")
    signing = receipt["signing"]
    require(isinstance(signing, dict) and set(signing) == {"private_key_exported", "certificate"}
            and signing["private_key_exported"] is False, "unsafe signing provenance")
    validate_record(signing["certificate"], "signing certificate")
    binding = {key: receipt[key] for key in (
        "commit", "variant", "kernel_release", "archive_sha256", "config",
        "baseline_config_sha256", "ordered_patches", "worker_sha256", "host", "target", "toolchain", "target_sysroot")}
    signature = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(receipt["input_signature"] == signature, "compile input signature differs")
    return pins


def decompress_image(source, destination, expected, release):
    """Stream exactly one gzip member, enforcing the enrolled window before allocation."""
    require(expected["bytes"] <= IMAGE_LIMIT, "recorded Image exceeds the enrolled kernel window")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    digest, size = hashlib.sha256(), 0
    header, found, overlap = bytearray(), False, b""
    needle = ("Linux version " + release + " ").encode("ascii")
    with source.open("rb") as compressed, destination.open("xb") as image:
        while block := compressed.read(CHUNK):
            require(not decoder.eof, "Image.gz has trailing data or multiple members")
            pending = block
            while pending:
                data = decoder.decompress(pending, min(CHUNK, IMAGE_LIMIT - size + 1))
                size += len(data)
                require(size <= IMAGE_LIMIT and size <= expected["bytes"],
                        "Image exceeds the enrolled kernel window/recorded size")
                if len(header) < 64:
                    header.extend(data[:64 - len(header)])
                window = overlap + data
                found = found or needle in window
                overlap = window[-len(needle):]
                digest.update(data)
                image.write(data)
                require(not decoder.unused_data, "Image.gz has trailing data or multiple members")
                pending = decoder.unconsumed_tail
        require(decoder.eof, "Image.gz is truncated")
    require(size == expected["bytes"] and digest.hexdigest() == expected["sha256"],
            "decompressed Image differs from compile receipt")
    require(len(header) == 64 and header[0x38:0x3c] == b"ARM\x64", "candidate is not an ARM64 Image")
    text_offset, image_size = struct.unpack_from("<QQ", header, 8)
    require(size <= image_size and 0 < text_offset + image_size <= IMAGE_LIMIT,
            "Image header exceeds the enrolled kernel window")
    require(found, "Image kernel release differs")


def module_record(stream, size, release, name):
    """Hash all signed bytes while checking ELF and vermagic without extracting modules."""
    digest, header, tail, overlap = hashlib.sha256(), bytearray(), b"", b""
    needle = b"vermagic=" + release.encode("ascii")
    found, count = False, 0
    while block := stream.read(CHUNK):
        count += len(block)
        digest.update(block)
        if len(header) < 64:
            header.extend(block[:64 - len(header)])
        window = overlap + block
        start = window.find(needle)
        while start >= 0:
            end = start + len(needle)
            found = found or (end < len(window) and window[end:end + 1] in (b" ", b"\0"))
            start = window.find(needle, start + 1)
        overlap = window[-len(needle) - 1:]
        tail = (tail + block)[-65536:]
    require(count == size, "truncated module: " + name)
    require(len(header) == 64 and header[:7] == b"\x7fELF\x02\x01\x01"
            and struct.unpack_from("<HHI", header, 16) == (1, 183, 1),
            "module is not AArch64 ELF: " + name)
    require(found, "module vermagic release differs: " + name)
    require(tail.endswith(SIGNATURE_MAGIC) and len(tail) >= len(SIGNATURE_MAGIC) + 12,
            "module signature is missing: " + name)
    info_offset = len(tail) - len(SIGNATURE_MAGIC) - 12
    info = tail[info_offset:info_offset + 12]
    signature_size = struct.unpack_from(">I", info, 8)[0]
    require(info[:8] == b"\0\0\x02\0\0\0\0\0"
            and 0 < signature_size <= info_offset and signature_size + 12 + len(SIGNATURE_MAGIC) < size,
            "module signature trailer differs: " + name)
    signature = tail[info_offset - signature_size:info_offset]
    return {"sha256": digest.hexdigest(), "bytes": count,
            "signature": {"sha256": hashlib.sha256(signature).hexdigest(),
                          "bytes": signature_size, "format": "PKCS#7"}}


def validate_modules(path, release):
    prefix = PurePosixPath("lib/modules") / release
    seen, files, ancestors, modules, dependencies = set(), set(), set(), {}, False
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            parts = name.split("/")
            require(name and "\\" not in name and not name.startswith("/")
                    and all(part not in ("", ".", "..") for part in parts),
                    "unsafe module archive path: " + member.name)
            relative = PurePosixPath(name)
            require(relative not in seen, "duplicate module archive path: " + name)
            seen.add(relative)
            require(member.isdir() or member.isfile(), "module archive contains a link or special file")
            require((member.isdir() and relative in prefix.parents)
                    or relative == prefix and member.isdir() or prefix in relative.parents,
                    "wrong module release in archive: " + name)
            require(not member.sparse, "sparse module archive entry: " + name)
            require(not any(parent in files for parent in relative.parents)
                    and not (member.isfile() and relative in ancestors),
                    "module archive file/directory path collision: " + name)
            ancestors.update(relative.parents)
            if member.isfile():
                files.add(relative)
            if not member.isfile():
                continue
            if relative.name == "modules.dep" and relative.parent == prefix:
                dependencies = True
            if relative.name.endswith(".ko"):
                with archive.extractfile(member) as stream:
                    modules[str(relative.relative_to(prefix))] = module_record(stream, member.size, release, name)
            else:
                require(not relative.name.endswith((".ko.gz", ".ko.xz", ".ko.zst")),
                        "module compression differs from pinned config")
    require(modules and dependencies, "missing modules or module dependency index")
    return dict(sorted(modules.items()))


def safety_policy(variant, candidate):
    if variant in candidate.TOUCHID_VARIANTS:
        return {"persistent_selection_allowed": True, "sensor_enabled": True,
                "enrollment_allowed": True, "matching_allowed": True,
                "required_module_options": {"xart_writes": 1, "provision_keybag": 1,
                                            "trusted_keys": 0, "filevault_api": 0, "xart_guard": 1}}
    return {"discovery_only": variant == "sep-probe", "sensor_auth_only": variant == "sep-sensor",
            "persistent_selection_allowed": False, "sensor_enabled": variant == "sep-sensor",
            "enrollment_allowed": False, "matching_allowed": False, "xart_writes": 0,
            "provision_keybag": 0, "trusted_keys": 0, "filevault_api": 0, "keystore_only": 1, "xart_guard": 2}


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def finalize(variant, output, baseline_dtb, worker_receipt, builder_arch):
    candidate = load_module("kernel_finalize_candidate", ROOT / "outputs/persistent-install/candidate_boot.py")
    require(variant in candidate.BUILD_DTB_VARIANTS, "unsupported locally transformed candidate variant")
    require(builder_arch in ("x86_64", "aarch64"), "unsupported builder architecture")
    output, baseline_dtb, worker_receipt = (Path(p).absolute() for p in (output, baseline_dtb, worker_receipt))
    require(output == output.resolve() and stat.S_ISDIR(output.lstat().st_mode),
            "expected a canonical completed output directory")
    regular(baseline_dtb)
    regular(worker_receipt)
    baseline = record(baseline_dtb)
    require(baseline_dtb not in [output / name for name in ARTIFACTS + METADATA],
            "baseline must not alias a generated artifact")
    require(not any((output / name).exists() or (output / name).is_symlink() for name in METADATA),
            "refusing an already finalized or partial metadata set")
    require(worker_receipt.stat().st_size <= CHUNK, "compile receipt is oversized")
    receipt = json.loads(worker_receipt.read_text(), object_pairs_hook=unique_object)
    pins = validate_receipt(receipt, variant, builder_arch, candidate)
    artifacts = receipt["artifacts"]
    image_present = (output / "Image").exists() or (output / "Image").is_symlink()
    for name in ARTIFACTS:
        if name == "Image" and not image_present:
            continue
        require(record(output / name) == artifacts[name], "candidate artifact differs: " + name)
    require((output / "kernel.release").read_bytes() == (pins["release"] + "\n").encode("ascii"),
            "kernel.release differs")
    receipt_record = record(worker_receipt)
    builder_sha256 = record(ROOT / "scripts/build-touchid-probe.sh")["sha256"]
    finalizer_sha256 = record(Path(__file__).resolve())["sha256"]
    transform_sha256 = record(ROOT / "scripts/prepare-sep-probe-dtb.py")["sha256"]
    with tempfile.TemporaryDirectory(prefix=".kernel-finalize-", dir=output) as directory:
        staged = Path(directory)
        decompress_image(output / "Image.gz", staged / "Image", artifacts["Image"], pins["release"])
        modules = validate_modules(output / "modules.tar.gz", pins["release"])
        transform_helper = load_module("kernel_finalize_dtb", ROOT / "scripts/prepare-sep-probe-dtb.py")
        shutil.copyfile(output / "t6050-j716s.dtb", staged / "t6050-j716s.dtb")
        transform = transform_helper.prepare(staged / "t6050-j716s.dtb", baseline_dtb)
        transformed = record(staged / "t6050-j716s.dtb")
        require(transform["input_sha256"] == artifacts["t6050-j716s.dtb"]["sha256"]
                and transform["baseline_sha256"] == baseline["sha256"]
                and transform["output_sha256"] == transformed["sha256"]
                and transform["helper_sha256"] == transform_sha256
                and transform["exact_node_names"] is True, "DTB transformation provenance differs")
        final_artifacts = dict(artifacts, **{"t6050-j716s.dtb": transformed})
        manifest = {"schema": 1, "commit": receipt["commit"], "variant": variant,
                    "qualification": receipt["qualification"], "kernel_release": pins["release"],
                    "artifacts": final_artifacts, "patches": receipt["patches"]}
        provenance = {"commit": receipt["commit"], "archive_sha256": receipt["archive_sha256"],
                      "config": receipt["config"], "ordered_patches": receipt["ordered_patches"],
                      "baseline_config_sha256": receipt["baseline_config_sha256"],
                      "builder_sha256": builder_sha256, "worker_sha256": receipt["worker_sha256"],
                      "finalizer_sha256": finalizer_sha256,
                      "compile_receipt": receipt_record, "input_signature": receipt["input_signature"],
                      "host": receipt["host"], "target": receipt["target"],
                      "target_sysroot": receipt["target_sysroot"],
                      "toolchain": {"guest": "Fedora 43 " + builder_arch, **receipt["toolchain"]},
                      "build_identity": receipt["build_identity"], "signing": receipt["signing"],
                      "modules": modules, "dtb_transform": transform, "safety": safety_policy(variant, candidate)}
        for name, value in ((METADATA[0], manifest), (METADATA[1], provenance)):
            (staged / name).write_bytes(json_bytes(value))
        sums = [final_artifacts[name]["sha256"] + "  " + name + "\n" for name in ARTIFACTS]
        sums += [record(staged / name)["sha256"] + "  " + name + "\n" for name in METADATA[:2]]
        (staged / "SHA256SUMS").write_text("".join(sums))
        # Publish only validated bytes. SHA256SUMS is the last completion marker.
        publish = ([] if image_present else ["Image"]) + ["t6050-j716s.dtb", *METADATA]
        for name in publish:
            with (staged / name).open("rb") as stream:
                os.fsync(stream.fileno())
        for name in publish:
            os.replace(staged / name, output / name)
        directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline-dtb", required=True, type=Path)
    parser.add_argument("--worker-receipt", required=True, type=Path)
    parser.add_argument("--builder-arch", required=True, choices=("x86_64", "aarch64"))
    args = parser.parse_args()
    try:
        manifest = finalize(args.variant, args.output, args.baseline_dtb, args.worker_receipt, args.builder_arch)
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, zlib.error) as error:
        parser.exit(1, "Kernel finalization refused: " + str(error) + "\n")
    print(json.dumps({"output": str(args.output.absolute()), "variant": manifest["variant"],
                      "kernel_release": manifest["kernel_release"], "artifacts": len(manifest["artifacts"])}, sort_keys=True))


if __name__ == "__main__":
    main()
