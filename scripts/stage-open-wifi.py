#!/usr/bin/env python3
"""Stage/arm a one-shot J716s Wi-Fi trial; never replace the configured boot payload."""
import argparse
from array import array
import fcntl
import hashlib
import importlib.util
import json
import mmap
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
SUPPORT = PROJECT / "outputs/persistent-install"
sys.path.insert(0, str(SUPPORT))
import candidate_boot

BASE = "7.3.0-j716s-8ab10100a4c1"
RELEASE = BASE + "-open-wifi"
TRIAL = "aurora/open-wifi.bin"
GUARD = Path("/usr/local/lib/j716s/j716s-bind-boot.py")
ESP = Path("/boot")
RECEIPT = Path("/etc/j716s/open-wifi.json")
WORK = PROJECT / "work/build/open-wifi-trial"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path):
    path = Path(path)
    return {"size": path.stat().st_size, "sha256": digest(path)}


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def check_regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.resolve() == path,
            "missing or unsafe file: " + str(path))


def atomic(path, data, mode=0o600):
    path = Path(path)
    require(path.parent.resolve() == path.parent, "symlinked destination parent")
    require(not path.is_symlink(), "symlinked destination")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            pending.replace(path)
            with open_directory(path.parent) as directory:
                os.fsync(directory)
        finally:
            pending.unlink(missing_ok=True)


class open_directory:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        return self.fd

    def __exit__(self, *unused):
        os.close(self.fd)


def marker_location(device):
    """Check the exact raw-block condition Aura requires, without writing the disk."""
    marker = ESP / "aura-try"
    check_regular(marker)
    data = marker.read_bytes()
    require(8 <= len(data) <= 4096, "trial marker has an invalid size")
    with marker.open("rb") as stream:
        block_size = array("i", [0])
        fcntl.ioctl(stream.fileno(), 2, block_size, True)  # FIGETBSZ
        block = array("i", [0])
        fcntl.ioctl(stream.fileno(), 1, block, True)  # FIBMAP: file block 0
    offset = block[0] * block_size[0]
    require(block[0] > 0 and offset % 4096 == 0, "marker's first block is not 4 KiB aligned")
    descriptor = os.open(device, os.O_RDONLY | os.O_DIRECT)
    try:
        # Block-device buffered reads can retain stale data after FAT file writes.
        with mmap.mmap(-1, 4096) as buffer:
            require(os.preadv(descriptor, [buffer], offset) == 4096,
                    "short direct read of the marker block")
            require(buffer[:len(data)] == data,
                    "marker does not match its on-disk extent")
    finally:
        os.close(descriptor)
    return data


def live_baseline():
    require(os.geteuid() == 0, "run through sudo or pkexec")
    require(os.uname().release == BASE, "stage from the known-working baseline kernel")
    require(b"apple,j716s" in Path("/proc/device-tree/compatible").read_bytes().split(b"\0"), "wrong board")
    require(run("findmnt", "-nro", "FSTYPE", "--target", str(ESP)) == "vfat", "ESP is not mounted as FAT")
    device = run("findmnt", "-nro", "SOURCE", "--target", str(ESP))
    identity = json.loads(Path("/usr/share/j716s/image-identity.json").read_text())
    deployment = json.loads((ESP / "aurora/deployment.json").read_text())
    for name in ("root.bin", "boot.bin", "rescue.bin"):
        check_regular(ESP / "aurora" / name)
        key = "rescue_sha256" if name == "rescue.bin" else "root_payload_sha256"
        require(digest(ESP / "aurora" / name) == deployment[key], "baseline payload differs: " + name)
    for item in deployment["payload_files"]:
        rel = PurePosixPath(item["path"])
        require(not rel.is_absolute() and ".." not in rel.parts and rel.parts[:2] in
                (("aurora", "root"), ("aurora", "rescue")), "unsafe baseline dependency")
        p = ESP / str(rel)
        check_regular(p)
        require(digest(p) == item["sha256"] and p.stat().st_size == item["size"], "baseline dependency differs")
    for name in ("asahi,m1n1-stage1-version", "asahi,m1n1-stage2-version"):
        value = (Path("/proc/device-tree/chosen") / name).read_bytes().rstrip(b"\0")
        require(value.startswith(b"1.0.0b12-g54678354"), "unreviewed Aura build: " + repr(value))
    marker = marker_location(device)
    require(marker.startswith(b"spent\n"), "a trial is already armed; refusing to replace it")
    return identity, deployment, device


def patched_guard(directory):
    """Patch a private copy of the *installed* guard, preserving its other checks."""
    check_regular(GUARD)
    target = Path(directory) / GUARD.name
    shutil.copyfile(GUARD, target)
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "--directory", str(directory),
                    "-i", str(PROJECT / "patches/kernel-open-wifi-runtime-guard.patch")], check=True)
    subprocess.run([sys.executable, str(target), "--check-only"], check=True,
                   env={**os.environ, "PYTHONPATH": str(SUPPORT)})
    return target.read_bytes()


def build_candidate(directory, identity, deployment, variant="open-wifi", baseline_config=None):
    boot_prefix, _ = candidate_boot.variant_paths(variant)
    pins = candidate_boot.VARIANTS[variant]
    release = pins["release"]
    kernel = PROJECT / ("outputs/kernel-" + variant + "-candidate")
    build = json.loads((kernel / "build-manifest.json").read_text())
    require(build["schema"] == 1 and build["commit"] == candidate_boot.KERNEL_COMMIT
            and build["kernel_release"] == release and build["variant"] == variant
            and build["qualification"] == "unqualified-experimental", "not the pinned experimental build")
    require(build["patches"] == pins["patches"] == {name: digest(PROJECT / "patches" / name)
            for name in pins["patches"]}, "driver/config patch differs")
    require(set(build["artifacts"]) == candidate_boot.BUILD_ARTIFACTS, "kernel artifact inventory differs")
    for name, item in build["artifacts"].items():
        require(name in {"Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map", "Module.symvers", "kernel.release", "modules.tar.gz"}, "unexpected kernel artifact")
        check_regular(kernel / name)
        require(record(kernel / name) == {"size": item["bytes"], "sha256": item["sha256"]}, "candidate artifact differs: " + name)
    baseline_artifacts = ((".config",) if variant in candidate_boot.ONE_SHOT_PARAMETERS else
                          ("t6050-j716s.dtb",) if variant == "bringup" else
                          () if variant == "touchid" else
                          (".config", "t6050-j716s.dtb"))
    for name in baseline_artifacts:
        require(build["artifacts"][name]["sha256"] == identity["kernel_artifacts"][name]["sha256"], "candidate changed baseline artifact: " + name)
    if variant in candidate_boot.CONFIG_VARIANTS:
        require(baseline_config is not None, variant + " requires the hash-bound baseline config")
        baseline_config = Path(baseline_config).resolve()
        check_regular(baseline_config)
        expected = identity["kernel_artifacts"][".config"]
        require(record(baseline_config) == {"size": expected["size_bytes"], "sha256": expected["sha256"]}, "baseline config differs")
        candidate_boot.validate_bluetooth_config(baseline_config.read_text(), (kernel / ".config").read_text())
    modules_root = Path(directory) / "modules"
    prefix = PurePosixPath("lib/modules") / release
    with tarfile.open(kernel / "modules.tar.gz", "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts, "unsafe module archive path")
            require(path in prefix.parents or path == prefix or prefix in path.parents, "wrong module release in archive")
            require(member.isdir() or member.isfile(), "module archive contains a link or special file")
        archive.extractall(modules_root, filter="data")
    module_dir = modules_root / str(prefix)
    modules = [{"path": str(p.relative_to(module_dir)), **record(p)}
               for p in sorted(module_dir.rglob("*")) if p.is_file()]
    require(modules and (module_dir / "modules.dep").is_file(), "missing module dependency index")
    blob = (ESP / "aurora/root.bin").read_bytes()
    boundary = blob.rfind(b"STACKBOTboot=")
    require(boundary >= 0, "baseline payload has no recognized bare Aura footer")
    boundary += len(b"STACKBOT")
    bare = blob[:boundary]
    old_spec, variables = blob[boundary:].split(b"\n", 1)
    expected_spec = "boot=" + deployment["esp_partition_uuid"] + ";aurora/root/Image.gz;aurora/root/t6050-j716s.dtb;aurora/root/initramfs.cpio.gz"
    require(old_spec.decode() == expected_spec, "baseline external-file recipe differs")
    lines = variables.decode("ascii").splitlines()
    require(sum(line.startswith("chosen.bootargs=") for line in lines) == 1, "ambiguous baseline boot arguments")
    require(not any("j716s.candidate=" in line for line in lines), "baseline is already a candidate")
    trial_id = secrets.token_hex(32)
    extra = " j716s.candidate=" + trial_id
    if variant in candidate_boot.ONE_SHOT_PARAMETERS:
        for line in lines:
            if line.startswith("chosen.bootargs="):
                existing = {token.partition("=")[0] for token in line.split("=", 1)[1].split()}
                require(not existing.intersection(candidate_boot.ONE_SHOT_PARAMETERS[variant]), "baseline already supplies SEP parameters")
        extra += " " + " ".join(key + "=" + value for key, value in candidate_boot.ONE_SHOT_PARAMETERS[variant].items())
    lines = [line + extra if line.startswith("chosen.bootargs=") else line for line in lines]
    encoded = ("\n".join(lines) + "\n").encode("ascii")
    spec = importlib.util.spec_from_file_location("trial_payload", PROJECT / "work/aura-audit/compose-linux-rescue.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    image = helpers.single_gzip((kernel / "Image.gz").read_bytes())
    require(image[0x38:0x3c] == b"ARM\x64", "candidate is not an ARM64 Image")
    text_offset, image_size = struct.unpack_from("<QQ", image, 8)
    require(len(image) <= image_size and 0 < text_offset + image_size <= 128 * 1024**2, "candidate exceeds the enrolled kernel window")
    dtb = kernel / "t6050-j716s.dtb" if variant in candidate_boot.BUILD_DTB_VARIANTS else ESP / "aurora/root/t6050-j716s.dtb"
    initramfs_path = "aurora/root/initramfs.cpio.gz" if variant == "sep-sensor" else boot_prefix + "/initramfs.cpio.gz"
    files = [(boot_prefix + "/Image.gz", (kernel / "Image.gz").read_bytes()),
             (boot_prefix + "/t6050-j716s.dtb", dtb.read_bytes()),
             (initramfs_path, (ESP / "aurora/root/initramfs.cpio.gz").read_bytes())]
    payload, _, inventory = helpers.boot_payload(bare, deployment["esp_partition_uuid"], files, encoded)
    if variant in candidate_boot.CONFIG_VARIANTS:
        for name, source in ((".config", kernel / ".config"), ("baseline.config", baseline_config)):
            data = source.read_bytes()
            files.append((boot_prefix + "/" + name, data))
            inventory.append({"path": boot_prefix + "/" + name, "size": len(data), "sha256": sha(data)})
    trial = boot_prefix + ".bin"
    inventory.append({"path": trial, "size": len(payload), "sha256": sha(payload)})
    receipt = {"schema": 1, "kind": "j716s-" + variant + "-trial", "trial_id": trial_id,
               "kernel_release": release, "baseline_identity_sha256": digest("/usr/share/j716s/image-identity.json"),
               "baseline_deployment_sha256": digest(ESP / "aurora/deployment.json"),
               "build_manifest": build, "files": inventory, "modules": modules}
    return [(name, data) for name, data in files if name != "aurora/root/initramfs.cpio.gz"] + [(trial, payload)], receipt, module_dir


def stage(apply):
    identity, deployment, device = live_baseline()
    require(not RECEIPT.exists(), "a candidate is already staged; inspect it rather than overwrite it")
    require(not (ESP / TRIAL).exists() and not (ESP / "aurora/open-wifi").exists(), "candidate ESP paths already exist")
    destination = Path("/usr/lib/modules") / RELEASE
    require(not destination.exists(), "candidate module directory already exists")
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK) as temporary:
        guard = patched_guard(temporary)
        files, receipt, modules = build_candidate(temporary, identity, deployment)
        needed = sum(len(data) for _, data in files) + 8 * 1024**2
        require(shutil.disk_usage(ESP).free >= needed, "insufficient ESP space; no old files will be deleted")
        print("Verified baseline root/rescue, candidate artifacts and one-shot marker extent.")
        if not apply:
            print("CHECK ONLY: no target files changed.")
            return
        backup = WORK / "baseline-guard.py"
        require(not backup.exists(), "original guard record already exists; inspect the previous attempt")
        atomic(backup, GUARD.read_bytes())
        shutil.copytree(modules, destination)
        (ESP / "aurora/open-wifi").mkdir()
        for name, data in files:
            atomic(ESP / name, data)
        atomic(RECEIPT, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
        helper = GUARD.parent / "candidate_boot.py"
        require(not helper.exists(), "candidate runtime helper already exists")
        atomic(helper, (SUPPORT / "candidate_boot.py").read_bytes(), 0o644)
        atomic(GUARD, guard, 0o755)
        candidate_boot.verify_candidate(Path("/"), BASE, identity, deployment)
        subprocess.run([sys.executable, str(GUARD), "--check-only"], check=True)
        subprocess.run(["sync", "-f", str(ESP)], check=True)
        subprocess.run(["sync", "-f", str(destination)], check=True)
        marker_location(device)
        print("STAGED: baseline boot selection unchanged. Use --arm for the one-shot trial.")


def arm():
    identity, deployment, device = live_baseline()
    candidate_boot.verify_candidate(Path("/"), BASE, identity, deployment)
    require(digest(GUARD.parent / "candidate_boot.py") == digest(SUPPORT / "candidate_boot.py"), "installed candidate validator differs")
    subprocess.run([sys.executable, str(GUARD), "--check-only"], check=True)
    subprocess.run(["sync", "-f", str(ESP)], check=True)
    marker = ESP / "aura-try"
    # In-place update preserves the already validated FAT allocation.
    data = ("armed\n" + TRIAL + "\n").encode("ascii")
    try:
        with marker.open("r+b") as stream:
            stream.write(data)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        subprocess.run(["sync", "-f", str(ESP)], check=True)
        require(marker_location(device) == data, "armed marker readback differs")
    except (OSError, ValueError):
        cancel()
        raise
    print("ARMED ONCE: next Linux boot selects " + RELEASE)
    print("No reboot performed. The following Linux boot returns to the configured baseline.")


def cancel():
    require(os.geteuid() == 0, "run through sudo or pkexec")
    marker = ESP / "aura-try"
    check_regular(marker)
    data = marker.read_bytes()
    require(data in [("armed\n" + TRIAL + "\n").encode(), ("spent\n" + TRIAL + "\n").encode()], "marker belongs to a different trial")
    with marker.open("r+b") as stream:
        stream.write(b"spent\n")
        stream.flush()
        os.fsync(stream.fileno())
    subprocess.run(["sync", "-f", str(ESP)], check=True)
    require(marker.read_bytes().startswith(b"spent\n"), "cancellation readback differs")
    print("CANCELLED: baseline boot payload remains selected.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stage", action="store_true", help="stage candidate without arming")
    modes.add_argument("--arm", action="store_true", help="arm an already staged candidate once")
    modes.add_argument("--cancel", action="store_true", help="cancel only this trial's marker")
    parser.add_argument("--acknowledge-risk", action="store_true", help="acknowledge the documented experimental boot and recovery risks")
    args = parser.parse_args()
    require(not (args.stage or args.arm) or args.acknowledge_risk, "staging/arming requires explicit --acknowledge-risk")
    if args.arm:
        arm()
    elif args.cancel:
        cancel()
    else:
        stage(args.stage)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        sys.exit(1)
