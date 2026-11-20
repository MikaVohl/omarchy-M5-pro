#!/usr/bin/env python3
"""Prepare only pinned public text-model inputs; never build or run inference."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

BASE = Path(__file__).resolve().parent
MANIFEST = BASE / "inputs.json"
ATTENTION = ("attention-accumulator.patch", "44feac04fa210e9c443fbebf58b9bb705f914acae9e89b056734b58929e22187")
BOREALIS = ("borealis-matmul.patch", "1873d56784bbfea75468d2051baa895a69208d9705233fd4a19a43bd6dbee737")
GEMV = ("borealis-gemv.patch", "495f2a67e38da573b259eeb7844fd2828fee70d5ddb65a5dfaf43ba195cc6e55")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_input(work, item, fetch):
    path = work / item["path"]
    if not path.exists():
        if not fetch:
            raise RuntimeError(f"Missing pinned input: {path}; prepare with --fetch")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".download-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(item["url"], timeout=120) as response:
                shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
            temporary = Path(temporary)
            if temporary.stat().st_size != item["size"] or sha256(temporary) != item["sha256"]:
                raise RuntimeError(f"Downloaded input fails pinned size/hash: {item['path']}")
            os.link(temporary, path)  # Fail rather than overwrite a concurrent file.
        finally:
            Path(temporary).unlink(missing_ok=True)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Not a regular private input: {path}")
    if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
        raise RuntimeError(f"Pinned input fails size/hash: {path}")


def tree_inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = {"link": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"sha256": sha256(path), "mode": path.stat().st_mode & 0o777}
        elif not path.is_dir():
            raise RuntimeError(f"Unexpected source-tree entry: {path}")
    return result


def prepare_tree(work, item, verify_only, patches=()):
    patches = patches if item["tree"] == "source" else ()
    patch_hashes = {name: digest for name, digest in patches}
    patch_bytes = {}
    for name, expected in patches:
        data = (BASE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Source patch differs from its pin: {name}")
        patch_bytes[name] = data
    tree = work / item["tree"]
    receipt = work / f"{item['tree']}-receipt.json"
    if tree.exists() or tree.is_symlink() or receipt.exists():
        if tree.is_symlink() or not tree.is_dir() or not receipt.is_file():
            raise RuntimeError(f"Incomplete/unsafe source preparation: {tree}")
        prior = json.loads(receipt.read_text())
        if (prior["archive_sha256"] != item["sha256"] or prior.get("patches", {}) != patch_hashes
                or "patch_sha256" in prior
                or prior["files"] != tree_inventory(tree)):
            raise RuntimeError(f"Prepared source differs from pinned tree receipt: {tree}")
        return
    if verify_only:
        raise RuntimeError(f"Missing prepared source tree: {tree}")
    stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=work))
    try:
        with tarfile.open(work / item["path"], "r:gz") as archive:
            for member in archive.getmembers():
                if member.name.split("/", 1)[0] != item["root"]:
                    raise RuntimeError(f"Unexpected archive root: {member.name}")
            archive.extractall(stage, filter="data")
        extracted = stage / item["root"]
        for data in patch_bytes.values():
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], cwd=extracted,
                           input=data, check=True)
        data = {"archive_sha256": item["sha256"], "files": tree_inventory(extracted)}
        if patch_hashes:
            data["patches"] = patch_hashes
        os.rename(extracted, tree)
        with receipt.open("x") as output:
            json.dump(data, output, indent=2, sort_keys=True)
            output.write("\n")
    finally:
        shutil.rmtree(stage)


def verify_toolchain(manifest, route):
    toolchain = manifest["toolchain"]
    if platform.machine() != toolchain["architecture"]:
        raise RuntimeError("Candidate requires the supported aarch64 host toolchain")
    cpuinfo = Path("/proc/cpuinfo").read_text()
    observed = [set(line.split(":", 1)[1].split()) for line in cpuinfo.splitlines() if line.startswith("Features")]
    if not observed or any(not set(toolchain["cpu_features"]).issubset(features) for features in observed):
        raise RuntimeError("Explicit ARM instruction contract is absent; do not spoof CPU features")
    packages = dict(toolchain["cpu_packages"])
    tools = dict(toolchain["cpu_tool_sha256"])
    if route == "vulkan":
        packages.update(toolchain["vulkan_packages"])
        tools.update(toolchain["vulkan_tool_sha256"])
    output = subprocess.check_output(["/usr/bin/pacman", "-Q", *packages], text=True)
    actual = dict(line.split(" ", 1) for line in output.splitlines())
    if actual != packages:
        raise RuntimeError(f"Installed toolchain differs from pinned package versions: {actual}")
    for filename, expected in tools.items():
        if sha256(Path(filename)) != expected:
            raise RuntimeError(f"Installed tool differs from pinned digest: {filename}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", nargs="?", type=Path, default=BASE / "../../work/build/llama-j716s")
    parser.add_argument("--fetch", action="store_true", help="Download only missing manifest-pinned public inputs")
    parser.add_argument("--verify-only", action="store_true", help="No downloads or source extraction")
    parser.add_argument("--route", choices=("cpu", "vulkan"), default="vulkan")
    parser.add_argument("--toolchain", action="store_true", help="Require the pinned installed toolchain")
    parser.add_argument("--variant", choices=("stock", "attention", "borealis", "borealis-gemv"), default="stock",
                        help="isolated Vulkan source variant; borealis-gemv adds experimental single-token tuning")
    parser.add_argument("--with-7b", action="store_true", help="also verify or fetch the 4.68 GB benchmark model")
    args = parser.parse_args()
    if args.fetch and args.verify_only:
        parser.error("--fetch and --verify-only are mutually exclusive")
    if args.variant != "stock" and args.route != "vulkan":
        parser.error("Source variants are Vulkan-only")
    patches = {"stock": (), "attention": (ATTENTION,), "borealis": (ATTENTION, BOREALIS),
               "borealis-gemv": (ATTENTION, BOREALIS, GEMV)}[args.variant]
    work = args.workspace.resolve()
    if not work.is_relative_to((BASE / "../../work/build").resolve()):
        parser.error("Workspace must stay inside this checkout's private work/build directory")
    work.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    archives = manifest["archives"] if args.route == "vulkan" else manifest["archives"][:1]
    with (work / ".prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for item in [*archives, *manifest["models"], *manifest["license_inputs"]]:
            verify_input(work, item, args.fetch)
        if args.with_7b:
            for item in json.loads((BASE / "benchmark-7b.json").read_text())["files"]:
                verify_input(work, item, args.fetch)
        for item in archives:
            prepare_tree(work, item, args.verify_only, patches)
        if args.toolchain:
            verify_toolchain(manifest, args.route)
    print(f"Pinned {args.route} inputs ready: {work}; no build or inference executed")


if __name__ == "__main__":
    main()
