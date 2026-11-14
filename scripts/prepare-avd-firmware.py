#!/usr/bin/env python3
"""Build/package the pinned open AVD v5-tier2 image for J716s 26C5035r.

Offline: no Apple IPSW, network, installation, device access or module loading.
Builds in disposable copies, checks boot vectors and the upstream host queue
unit test, and requires two byte-identical images before publishing a candidate.
A successful build is not physical decode qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "ea3a65cc9c0c41907e6ad2714b813e4be6858485"
SOURCE_URL = "https://codeload.github.com/aurora-silicon/avd-fw/tar.gz/" + SOURCE_COMMIT
SOURCE_SHA256 = "e266022f0bedf531431be50efcbe23f29a10efc4b0a64468abcc6c3f7e364b19"
SOURCE_PREFIX = "avd-fw-" + SOURCE_COMMIT
KERNEL_COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
DRIVER_SHA256 = {
    "avd-drv.c": "09c464ec94ef9fcc2af20a669286e6cc236a1540fdff8386d41be60eb7a89ea7",
    "avd-backend-open.c": "8740172fc26c540abe6d2daf943d82aed3dd80bd9818589be02b4c54f1f87113",
    "avd-backend-queue.c": "fa618dabbdcb93a2b5d48ecba7388d69834de86f5dcea5d4e8834e12f85e9f57",
}
FIRMWARE_NAME = "apple/avd-fw-v5-t2.bin"
IMAGE_SIZE = 0x12000
RECIPE = "j716s-open-avd-v5-tier2-v1"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular, non-symlink input: " + str(path))
    return path


def profile():
    expected = json.loads((PROJECT / "profiles/j716s.json").read_text())
    if (expected["model"], expected["board"], expected["soc"], expected["macos_build"]) != (
            "Mac17,8", "j716s", "t6050", "26C5035r"):
        raise ValueError("Not the exact J716s / T6050 / 26C5035r profile")
    if expected["sources"]["kernel"]["commit"] != KERNEL_COMMIT:
        raise ValueError("Kernel pin changed: requalify AVD source/queue compatibility")
    return expected


def driver_inputs(kernel):
    directory = kernel / "drivers/media/platform/apple/avd"
    for name, digest in DRIVER_SHA256.items():
        if sha(regular_file(directory / name).read_bytes()) != digest:
            raise ValueError("AVD driver differs from the pinned queue ABI 2 source: " + name)
    return {"commit": KERNEL_COMMIT, "sha256": DRIVER_SHA256,
            "queue_abi": 2, "sram_aperture": "0x01094000"}


def extract_source(archive, destination):
    # Verify bytes before any archive parser or source build is invoked.
    with regular_file(archive).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("AVD source archive SHA-256 differs from the pinned commit")
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as bundle:
            seen = set()
            for member in bundle.getmembers():
                parts = PurePosixPath(member.name).parts
                if (not parts or parts[0] != SOURCE_PREFIX or ".." in parts or
                        member.name.startswith("/") or member.name in seen or
                        not (member.isdir() or member.isfile())):
                    raise ValueError("Unsafe AVD source archive member: " + member.name)
                seen.add(member.name)
            bundle.extractall(destination, filter="data")
    return destination / SOURCE_PREFIX


def validate_image(data, start=None):
    if len(data) != IMAGE_SIZE:
        raise ValueError("AVD v5-tier2 firmware must be exactly 0x12000 bytes")
    sp, reset = struct.unpack_from("<II", data)
    if sp != 0x10000000 + IMAGE_SIZE or not reset & 1 or not 8 <= (reset & ~1) < IMAGE_SIZE:
        raise ValueError("AVD firmware has invalid CM3 stack/reset vectors")
    if start is not None and reset != (start | 1):
        raise ValueError("AVD reset vector does not point at ELF _start")
    return {"bytes": len(data), "sha256": sha(data),
            "stack_pointer": hex(sp), "reset_vector": hex(reset)}


def checked_package(directory):
    """Validate a locally built candidate before merging it into an IPSW package."""
    expected = profile()
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or (directory / "apple").is_symlink():
        raise ValueError("AVD package must be a real directory")
    report = json.loads(regular_file(directory / "avd-build-report.json").read_text())
    source = report.get("source", {})
    if (report.get("schema"), report.get("recipe"), report.get("target_model"),
            report.get("board"), report.get("soc"), report.get("macos_build")) != (
            1, RECIPE, expected["model"], expected["board"], expected["soc"], expected["macos_build"]):
        raise ValueError("AVD package identity/recipe differs from the exact profile")
    if source != {"url": SOURCE_URL, "commit": SOURCE_COMMIT,
                  "archive_sha256": SOURCE_SHA256, "license": "MIT"}:
        raise ValueError("AVD package has wrong source provenance")
    if (report.get("driver") != {"commit": KERNEL_COMMIT, "sha256": DRIVER_SHA256,
                                 "queue_abi": 2, "sram_aperture": "0x01094000"} or
            report.get("reproducible_images") is not True or
            report.get("upstream_host_queue_test") != "passed" or
            report.get("physical_qualification") != "not performed"):
        raise ValueError("AVD package has invalid build/qualification metadata")
    image = regular_file(directory / FIRMWARE_NAME).read_bytes()
    if report.get("firmware_name") != FIRMWARE_NAME or report.get("image") != validate_image(image):
        raise ValueError("AVD package image differs from its build report")
    elf = regular_file(directory / "avd-fw-v5-t2.elf").read_bytes()
    if report.get("elf_sha256") != sha(elf):
        raise ValueError("AVD package ELF differs from its build report")
    manifest = json.loads(regular_file(directory / "firmware-manifest.json").read_text())
    if manifest != package_manifest(expected, report):
        raise ValueError("AVD firmware manifest differs from its build report")
    return image, report


def package_manifest(expected, report):
    return {"schema": 1, "target_model": expected["model"], "board": expected["board"],
            "soc": expected["soc"], "macos_build": expected["macos_build"],
            "firmware": {FIRMWARE_NAME: report["image"]["sha256"]},
            "avd": report, "apple_firmware_input": None,
            "physical_qualification": "not performed"}


def tool(command):
    resolved = shutil.which(command)
    if resolved is None:
        raise ValueError("Missing build prerequisite: " + command)
    return str(Path(resolved).resolve())


def build(archive, output, kernel, cc="clang", objcopy="llvm-objcopy", nm="llvm-nm", hostcc="cc"):
    expected = profile()
    driver = driver_inputs(kernel)
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing existing firmware output directory")
    tools = {name: tool(command) for name, command in
             (("make", "make"), ("cmp", "cmp"), ("cc", cc), ("objcopy", objcopy), ("nm", nm), ("hostcc", hostcc))}
    versions = {name: {"path": path, "version": subprocess.check_output(
        [path, "--version"], text=True).splitlines()[0]} for name, path in tools.items()}
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKEFILES")}
    environment.update(LC_ALL="C", TZ="UTC")
    with tempfile.TemporaryDirectory(prefix="j716s-avd-") as temporary:
        scratch = Path(temporary)
        images = []
        elf_data = None
        for index in range(2):
            source = extract_source(archive, scratch / str(index))
            command = [tools["make"], "-f", "Makefile", "AVD_VER=5", "AVD_TIER=2",
                       "CC=" + tools["cc"], "OBJCOPY=" + tools["objcopy"]]
            subprocess.run(command, cwd=source, env=environment, check=True)
            binary = source / "build/avd-fw-v5-t2.bin"
            elf = binary.with_suffix(".elf")
            symbols = subprocess.check_output([tools["nm"], str(elf)], text=True)
            starts = [int(fields[0], 16) for line in symbols.splitlines()
                      if len(fields := line.split()) == 3 and fields[1:] == ["T", "_start"]]
            if len(starts) != 1:
                raise ValueError("Expected one ELF _start text symbol")
            data = binary.read_bytes()
            validate_image(data, starts[0])
            images.append(data)
            if index == 0:
                elf_data = elf.read_bytes()
                subprocess.run([tools["make"], "-f", "Makefile", "test", "HOSTCC=" + tools["hostcc"]],
                               cwd=source, env=environment, check=True)
        if images[0] != images[1]:
            raise ValueError("AVD firmware differs between clean builds")
        report = {"schema": 1, "recipe": RECIPE, "target_model": expected["model"],
                  "board": expected["board"], "soc": expected["soc"], "macos_build": expected["macos_build"],
                  "source": {"url": SOURCE_URL, "commit": SOURCE_COMMIT,
                             "archive_sha256": SOURCE_SHA256, "license": "MIT"},
                  "driver": driver, "toolchain": versions,
                  "firmware_name": FIRMWARE_NAME, "image": validate_image(images[0]),
                  "elf_sha256": sha(elf_data), "reproducible_images": True,
                  "upstream_host_queue_test": "passed", "physical_qualification": "not performed"}
        # Publish only after every build check passes. mkdir refuses a racing output,
        # including an empty directory; no existing candidate is overwritten.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir(mode=0o700)
        (output / "apple").mkdir(mode=0o700)
        (output / FIRMWARE_NAME).write_bytes(images[0])
        (output / "avd-fw-v5-t2.elf").write_bytes(elf_data)
        for filename, value in (("avd-build-report.json", report),
                                ("firmware-manifest.json", package_manifest(expected, report))):
            with (output / filename).open("x") as stream:
                stream.write(json.dumps(value, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, default=PROJECT / ("work/cache/avd-fw-" + SOURCE_COMMIT + ".tar.gz"))
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/firmware/j716s-26C5035r-avd")
    parser.add_argument("--kernel", type=Path, default=PROJECT / "work/sources/kernel")
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--objcopy", default="llvm-objcopy")
    parser.add_argument("--nm", default="llvm-nm")
    parser.add_argument("--hostcc", default="cc")
    args = parser.parse_args()
    print(json.dumps(build(args.source_archive, args.output, args.kernel,
                           args.cc, args.objcopy, args.nm, args.hostcc), indent=2))


if __name__ == "__main__":
    main()
