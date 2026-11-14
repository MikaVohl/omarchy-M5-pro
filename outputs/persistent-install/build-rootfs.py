#!/usr/bin/env python3
"""Build the complete persistent Omarchy/J716s ext4 root inside a separate ARM64 guest.

Never run as the macOS host or in the kernel-build guest. A root-owned native
Linux/aarch64 image guest needs CAP_SYS_ADMIN, python3, curl/CA certificates,
bsdtar for extraction, GNU tar for supplied modules, and e2fsprogs >= 1.47.1.
chroot bootstraps signed ALARM packages and Omarchy's pinned public signing keys.
No host sudo, physical disk, firmware, LocalPolicy or reboot operations occur.

Example (paths are inside the image guest; /exchange is this workspace):
  python3 /exchange/outputs/persistent-install/build-rootfs.py \\
    --guest-build J716S_OFFLINE_IMAGE \\
    --work-dir /work/rootfs --kernel-dir /exchange/outputs/kernel-candidate \\
    --firmware-dir /exchange/outputs/firmware/j716s-26C5035r-linux \\
    --mesa-build-script /exchange/outputs/persistent-install/build-mesa-j716s.sh \\
    --mesa-workspace /work/rootfs/mesa-work \\
    --out /exchange/outputs/rootfs/omarchy-j716s.img.gz --size-gib 32

The builder requires verified HTTPS transport, the upstream corruption checksum,
and a detached root-tarball signature from the pinned official ARM build-system
key before extraction/execution. Runtime packages require pacman signatures.
The random actual ext4 UUID is embedded before export; gzip's header is deterministic.
The root is not a byte-reproducible build. The retained sparse image is fsck
checked by the actual build, and the external manifest reports that result.

Boot acknowledgement is the supplied kernel's standard driver responsibility.
No userspace PMU/nvmem writes or invented boot-ack unit is installed; hardware
acknowledgement still requires parent/physical qualification of that kernel.
"""
import argparse
import contextlib
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import sys
import time
import tempfile
import urllib.request
import uuid

ALARM_URL = "https://ca.us.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz"
ALARM_BUILD_FINGERPRINT = "68B3537F39A313B3E574D06777193F152BDBE6A6"
BOOT_DENY = re.compile(r"^(linux-aarch64(?:-.*)?|linux-firmware(?:-.*)?)$")
GIB = 1 << 30


def run(*args, **kwargs):
    return subprocess.run([str(item) for item in args], check=True, **kwargs)


def capture(*args):
    return subprocess.check_output([str(item) for item in args], text=True).strip()


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            value.update(block)
    return value.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        if not url.startswith("https://"):
            raise RuntimeError("refusing non-HTTPS source redirect: " + url)
        return super().redirect_request(request, response, code, message, headers, url)


def fetch(url, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".download")
    opener = urllib.request.build_opener(HTTPSOnlyRedirect())
    try:
        with opener.open(url, timeout=120) as response, temporary.open("wb") as output:
            resolved = response.geturl()
            if not resolved.startswith("https://"):
                raise RuntimeError("source resolved to non-HTTPS URL")
            shutil.copyfileobj(response, output, 8 << 20)
        temporary.replace(path)
        return resolved
    finally:
        temporary.unlink(missing_ok=True)


def verify_alarm_signature(cache, source):
    signature = cache / "ArchLinuxARM-aarch64-latest.tar.gz.sig"
    resolved = ALARM_URL + ".sig"
    if not signature.exists():
        resolved = fetch(resolved, signature)
    key = Path(__file__).resolve().parent / "keys/archlinuxarm-builder.asc"
    with tempfile.TemporaryDirectory(prefix="root-signature.", dir=cache) as directory:
        home = Path(directory)
        shown = subprocess.check_output(["gpg", "--batch", "--no-options", "--homedir", str(home),
                                         "--show-keys", "--with-colons", str(key)], text=True)
        fingerprints, primary = [], False
        for row in shown.splitlines():
            fields = row.split(":")
            if fields[0] == "pub":
                primary = True
            elif fields[0] == "sub":
                primary = False
            elif fields[0] == "fpr" and primary:
                fingerprints.append(fields[9])
                primary = False
        if fingerprints != [ALARM_BUILD_FINGERPRINT]:
            raise RuntimeError("Official ARM root signing key fingerprint differs")
        keyring = home / "root-keyring.gpg"
        run("gpg", "--batch", "--no-options", "--homedir", home, "--dearmor", "--output", keyring, key)
        checked = subprocess.run(["gpgv", "--homedir", str(home), "--status-fd", "1", "--keyring", str(keyring),
                                  str(signature), str(source)], capture_output=True, text=True)
        valid = [line.split() for line in checked.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
        if checked.returncode or len(valid) != 1 or valid[0][-1] != ALARM_BUILD_FINGERPRINT:
            raise RuntimeError("ARM root detached signature refused: " + checked.stderr)
        return {"signature_url": resolved, "signature_sha256": sha256(signature),
                "signing_key_fingerprint": ALARM_BUILD_FINGERPRINT, "signing_key_sha256": sha256(key),
                "signing_key_source": "archlinuxarm/archlinuxarm-keyring@91e6b11698f8df66042d56aaa56fbe9c9263847d/packager/builder.asc",
                "gpgv_status": checked.stdout, "signature_verified": True}


def alarm_source(cache, tarball, checksum):
    source = Path(tarball) if tarball else cache / "ArchLinuxARM-aarch64-latest.tar.gz"
    sums = Path(checksum) if checksum else cache / "ArchLinuxARM-aarch64-latest.tar.gz.md5"
    provenance_path = cache / "alarm-download.json"
    if tarball and not checksum:
        raise RuntimeError("a supplied --tarball also requires its upstream --checksum")
    provenance = {}
    if provenance_path.is_file() and not tarball:
        provenance = json.loads(provenance_path.read_text())
    if not sums.exists():
        provenance["checksum_resolved_url"] = fetch(ALARM_URL + ".md5", sums)
    if not source.exists():
        provenance["tarball_resolved_url"] = fetch(ALARM_URL, source)
    text = sums.read_text().strip()
    matches = re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])", text)
    if len(matches) != 1:
        raise RuntimeError("upstream MD5 response must contain exactly one digest")
    md5 = hashlib.md5(usedforsecurity=False)
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            md5.update(block)
    if md5.hexdigest() != matches[0].lower():
        raise RuntimeError("ALARM tarball does not match retained upstream MD5")
    signature_record = verify_alarm_signature(cache, source)
    provenance.update(url=ALARM_URL, checksum_url=ALARM_URL + ".md5",
                      tarball_file=source.name, size_bytes=source.stat().st_size,
                      sha256=sha256(source), upstream_md5=matches[0].lower(),
                      checksum_text=text, checksum_sha256=sha256(sums),
                      authentication="HTTPS transport and pinned official build-system detached signature; MD5 corruption check only",
                      supplied_local_cache=bool(tarball))
    provenance.update(signature_record)
    save_json(provenance_path, provenance)
    return source, provenance


def safe_archive(path, module_release=None):
    count = 0
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            parts = PurePosixPath(name).parts
            if member.name.startswith("/") or ".." in parts:
                raise RuntimeError("unsafe archive member: " + member.name)
            if module_release:
                expected = "lib/modules/" + module_release
                alternate = "usr/" + expected
                allowed = (name in ("", ".", "lib", "lib/modules", "usr", "usr/lib", "usr/lib/modules")
                           or name == expected or name.startswith(expected + "/")
                           or name == alternate or name.startswith(alternate + "/"))
                if not allowed or not (member.isdir() or member.isreg() or member.issym() or member.islnk()):
                    raise RuntimeError("module archive contains unrelated content: " + member.name)
                if member.isreg() and (".ko" in name):
                    count += 1
    if module_release and count == 0:
        raise RuntimeError("supplied module archive contains no kernel modules")
    return count


@contextlib.contextmanager
def chroot_mounts(root, package_cache):
    mounts = []
    try:
        # Pacman's CheckSpace reads /proc mountinfo. The chroot needs its own
        # visible root mount, not an invisible subdirectory of the guest root.
        run("mount", "--bind", root, root)
        run("mount", "--make-private", root)
        mounts.append(root)
        for relative, filesystem in (("proc", "proc"), ("sys", "sysfs")):
            target = root / relative
            target.mkdir(exist_ok=True)
            run("mount", "-t", filesystem, filesystem, target)
            mounts.append(target)
        # Do not bind the guest's shared devtmpfs (PID1 keeps its handles open).
        # Image package scripts need only these harmless devices, not block disks.
        target = root / "dev"
        target.mkdir(exist_ok=True)
        run("mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", target)
        mounts.append(target)
        for name, major, minor in (("null", 1, 3), ("zero", 1, 5), ("full", 1, 7),
                                  ("random", 1, 8), ("urandom", 1, 9), ("tty", 5, 0)):
            os.mknod(target / name, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        for name, destination in (("fd", "/proc/self/fd"), ("stdin", "fd/0"),
                                  ("stdout", "fd/1"), ("stderr", "fd/2"), ("ptmx", "pts/ptmx")):
            (target / name).symlink_to(destination)
        pts = target / "pts"
        pts.mkdir()
        run("mount", "-t", "devpts", "-o", "newinstance,ptmxmode=0666,mode=0620", "devpts", pts)
        mounts.append(pts)
        shm = target / "shm"
        shm.mkdir()
        run("mount", "-t", "tmpfs", "-o", "mode=1777", "tmpfs", shm)
        mounts.append(shm)
        package_cache.mkdir(parents=True, exist_ok=True)
        cache_mount = root / "var/cache/pacman/pkg"
        cache_mount.mkdir(parents=True, exist_ok=True)
        run("mount", "--bind", package_cache, cache_mount)
        run("mount", "--make-private", cache_mount)
        mounts.append(cache_mount)
        resolver = root / "etc/resolv.conf"
        resolver.unlink(missing_ok=True)
        shutil.copyfile("/etc/resolv.conf", resolver)
        yield
    finally:
        # Only agents belonging to this image are stopped. Their open /dev
        # handles otherwise keep the private bind mounts busy after failures.
        if (root / "usr/bin/gpgconf").is_file():
            for home in ("/etc/pacman.d/gnupg", "/root/.gnupg"):
                if (root / home.lstrip("/")).is_dir():
                    run("chroot", root, "/usr/bin/gpgconf", "--homedir", home, "--kill", "all")
        # The namespace PID1 owns/reaps every daemon created by image tools;
        # never depend on the guest's PID1 (which may be sleep, not an init).
        stop_namespace_children()
        for target in reversed(mounts):
            run("umount", target)


def chroot(root, *args, env=None):
    environment = dict(os.environ)
    environment.update(PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", LC_ALL="C.UTF-8", SYSTEMD_OFFLINE="1")
    if env:
        environment.update(env)
    return run("chroot", root, *args, env=environment)


def install_file(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def remove_path(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def scrub_credentials(root):
    users = capture("chroot", root, "getent", "passwd").splitlines()
    for row in users:
        fields = row.split(":")
        if fields[0] == "alarm" or (int(fields[2]) >= 1000 and fields[0] != "nobody"):
            chroot(root, "userdel", "--remove", fields[0])
    chroot(root, "usermod", "--password", "!", "root")
    for relative in ("root/.ssh", "root/.gnupg", "home/alarm", "etc/machine-id", "var/lib/dbus/machine-id", "var/lib/systemd/random-seed"):
        remove_path(root / relative)
    (root / "etc/machine-id").touch(mode=0o444)
    dbus_identity = root / "var/lib/dbus/machine-id"
    dbus_identity.parent.mkdir(parents=True, exist_ok=True)
    dbus_identity.symlink_to("/etc/machine-id")
    for key in (root / "etc/ssh").glob("ssh_host_*"):
        remove_path(key)
    for directory in (root / "home").glob("*/.ssh"):
        remove_path(directory)
    for path in [root / "etc/sudoers", *(root / "etc/sudoers.d").glob("*")]:
        if path.is_file() and not path.is_symlink():
            lines = path.read_text().splitlines(keepends=True)
            path.write_text("".join(line for line in lines if line.lstrip().startswith("#") or
                                    ("NOPASSWD" not in line and "!authenticate" not in line)))
    sudo_rule = root / "etc/sudoers.d/10-j716s-owner"
    sudo_rule.parent.mkdir(parents=True, exist_ok=True)
    sudo_rule.write_text("%wheel ALL=(ALL:ALL) ALL\n")
    sudo_rule.chmod(0o440)
    ssh = root / "etc/ssh/sshd_config"
    ssh.parent.mkdir(parents=True, exist_ok=True)
    original = ssh.read_text() if ssh.exists() else ""
    original = "\n".join(line for line in original.splitlines()
                         if not re.match(r"^\s*(?:PermitRootLogin|PermitEmptyPasswords)\s+", line))
    # OpenSSH uses the first obtained value, before any Include/Match blocks.
    ssh.write_text("PermitRootLogin no\nPermitEmptyPasswords no\n" + original + "\n")
    chroot(root, "systemctl", "disable", "sshd.service")
    chroot(root, "systemctl", "mask", "sshd.service")
    chroot(root, "visudo", "-c")


def tree_records(path, prefix=""):
    records = {}
    for file in sorted(path.rglob("*")):
        relative = prefix + str(file.relative_to(path))
        if file.is_symlink():
            records[relative] = {"symlink": os.readlink(file)}
        elif file.is_file():
            records[relative] = {"sha256": sha256(file), "size_bytes": file.stat().st_size}
    return records


def record_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def enter_build_namespace():
    if os.getpid() != 1:
        os.execvp("unshare", ["unshare", "--mount", "--pid", "--fork", "--kill-child",
                             "--mount-proc", "--propagation", "private", sys.executable, *sys.argv])


def stop_namespace_children():
    if os.getpid() != 1:
        raise RuntimeError("image cleanup requires its own PID namespace init")
    try:
        os.kill(-1, 15)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            continue
        if time.monotonic() >= deadline:
            os.kill(-1, 9)
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    return
        time.sleep(0.05)


def unmount_inherited_root(root):
    result = subprocess.run(["findmnt", "--json", "--submounts", "--mountpoint", str(root)], capture_output=True, text=True)
    if result.returncode == 1:
        return
    if result.returncode:
        raise RuntimeError("cannot enumerate inherited image mounts: " + result.stderr)
    def unmount(item):
        for child in reversed(item.get("children", [])):
            unmount(child)
        target = Path(item["target"])
        if target != root and root not in target.parents:
            raise RuntimeError("inherited mount escapes the image root")
        run("umount", target)
    for item in json.loads(result.stdout)["filesystems"]:
        unmount(item)


def validate_staged_root(root, kernel, firmware, release):
    if (root / "etc/j716s/kernel.release").read_text().strip() != release:
        raise RuntimeError("staged root kernel identity differs")
    for name in ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map", "Module.symvers", "kernel.release"):
        if sha256(root / "usr/lib/j716s/kernel" / name) != sha256(kernel / name):
            raise RuntimeError("staged kernel artifact differs: " + name)
    supplied_firmware = tree_records(firmware / "apple")
    installed_firmware = tree_records(root / "usr/lib/firmware/apple")
    if any(installed_firmware.get(name) != record for name, record in supplied_firmware.items()):
        raise RuntimeError("staged J716s firmware differs from supplied firmware")
    if sorted(path.name for path in (root / "usr/lib/modules").iterdir()) != [release]:
        raise RuntimeError("staged module release differs")
    with tarfile.open(kernel / "modules.tar.gz") as archive:
        for member in archive:
            if member.isreg() and re.search(r"\.ko(?:\.(?:gz|xz|zst))?$", member.name):
                relative = member.name.removeprefix("./").removeprefix("usr/")
                data = archive.extractfile(member)
                if hashlib.file_digest(data, "sha256").hexdigest() != sha256(root / "usr" / relative):
                    raise RuntimeError("staged kernel module differs: " + member.name)
    receipt = json.loads((root / "var/lib/j716s/omarchy-image.json").read_text())
    if receipt.get("schema") != 1 or receipt.get("board") != "j716s" or not receipt.get("packages"):
        raise RuntimeError("unsupported signed Omarchy stage receipt")
    packages = dict(line.split(" ", 1) for line in capture("chroot", root, "pacman", "-Q").splitlines())
    if any(packages.get(name) != version for name, version in receipt["packages"].items()):
        raise RuntimeError("staged signed package receipt versions differ")
    if any(BOOT_DENY.fullmatch(name) for name in packages):
        raise RuntimeError("staged root contains generic kernel/firmware ownership")
    # Official desktop manifests may add package-owned video firmware; that
    # does not replace any supplied J716s wireless/trackpad bytes.
    for name in installed_firmware.keys() - supplied_firmware.keys():
        owner = capture("chroot", root, "pacman", "-Qqo", "/usr/lib/firmware/apple/" + name)
        if owner not in receipt["packages"]:
            raise RuntimeError("extra staged firmware has no signed package provenance: " + name)
    return receipt


def validate_mesa_stage(root):
    prefix = root / "opt/mesa-j716s"
    receipt_path = prefix / "share/j716s/build-receipt.json"
    inventory_path = prefix / "share/j716s/prefix-inventory.json"
    receipt = json.loads(receipt_path.read_text())
    inventory = json.loads(inventory_path.read_text())
    if (receipt.get("architecture") != "aarch64" or receipt.get("mesa_commit") != "a8e86a80cdc4695d00e7e63b8ebea263e557431b"
            or receipt.get("source_archive_sha256") != "fa866896387c27e57263e4f1c7764d3daa3b4d2de7a6d1f04da4ecb25f73b421"
            or receipt.get("inventory_sha256") != sha256(inventory_path) or receipt.get("source_patches") != []):
        raise RuntimeError("native Mesa build receipt differs from the pinned ARM64 source")
    for item in inventory["files"]:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("unsafe native Mesa inventory path")
        path = prefix / relative
        if "symlink" in item:
            if not path.is_symlink() or os.readlink(path) != item["symlink"]:
                raise RuntimeError("native Mesa symlink differs: " + item["path"])
        elif sha256(path) != item["sha256"]:
            raise RuntimeError("native Mesa installed file differs: " + item["path"])
    return sha256(receipt_path), sha256(inventory_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guest-build", required=True, choices=["J716S_OFFLINE_IMAGE"])
    parser.add_argument("--work-dir", default="/work/rootfs")
    parser.add_argument("--kernel-dir", required=True)
    parser.add_argument("--firmware-dir", required=True)
    parser.add_argument("--mesa-build-script", required=True)
    parser.add_argument("--mesa-workspace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--tarball")
    parser.add_argument("--checksum")
    parser.add_argument("--size-gib", type=int, default=32)
    parser.add_argument("--resume-from", choices=["omarchy", "mesa"], help="Continue a validated offline image-build stage; never a physical deployment")
    args = parser.parse_args()
    if os.uname().sysname != "Linux" or os.uname().machine != "aarch64" or os.geteuid() != 0:
        raise RuntimeError("run as root only in the separate native Linux/ARM64 image guest; never host sudo")
    for device_tree in (Path("/proc/device-tree/compatible"), Path("/sys/firmware/devicetree/base/compatible")):
        if device_tree.is_file() and any(item.startswith(b"apple,") for item in device_tree.read_bytes().split(b"\0")):
            raise RuntimeError("image guest must not expose a physical Apple device tree")
    if Path("/src/linux").exists() or Path("/build/.config").exists():
        raise RuntimeError("use a separate image guest, not the retained kernel-build guest")
    capabilities = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if not int(capabilities["CapEff"].strip(), 16) & (1 << 21):
        raise RuntimeError("isolated image guest needs --cap-add CAP_SYS_ADMIN for chroot mounts")
    enter_build_namespace()
    for binary in ("unshare", "findmnt", "bsdtar", "tar", "chroot", "mount", "umount", "mke2fs", "e2fsck", "blkid", "gpg", "gpgv"):
        if not shutil.which(binary):
            raise RuntimeError("missing guest dependency: " + binary)
    work = Path(args.work_dir).resolve()
    root = work / "root"
    if str(work) in ("/", "/work", "/exchange", "/workspace") or root.is_symlink():
        raise RuntimeError("choose a dedicated image-build work directory")
    if root.exists() != bool(args.resume_from):
        raise RuntimeError("existing root requires explicit --resume-from; fresh builds require no root/")
    out = Path(args.out).resolve()
    if out.suffix != ".gz":
        raise RuntimeError("--out must name an .img.gz file")
    image = out.with_suffix("")
    if out.exists() or image.exists():
        raise RuntimeError("output image already exists; choose a fresh output instead of replacing it")
    if args.size_gib < 16:
        raise RuntimeError("the complete Omarchy root requires at least a 16 GiB image")
    here = Path(__file__).resolve().parent
    kernel = Path(args.kernel_dir).resolve()
    firmware = Path(args.firmware_dir).resolve()
    from persistent_payload import kernel_build, loader_build
    kernel_record, kernel_hash = kernel_build(kernel)
    _, loader_hash = loader_build()
    release = kernel_record["kernel_release"]
    artifacts = {name: {"sha256": item["sha256"], "size_bytes": item["bytes"]}
                 for name, item in kernel_record["artifacts"].items()}
    module_count = safe_archive(kernel / "modules.tar.gz", release)
    if not (firmware / "apple/tpmtfw-j716s.bin").is_file() or not (firmware / "apple/centauri/j716s-ftab.bin").is_file():
        raise RuntimeError("matching J716s trackpad/N1 firmware is required")
    firmware_provenance = json.loads((firmware / "n1-build-report.json").read_text())
    if (firmware_provenance.get("schema") != 1 or firmware_provenance.get("macos_build") != "26C5035r"
            or firmware_provenance.get("target_model") != "Mac17,8" or firmware_provenance.get("board") != "j716s"):
        raise RuntimeError("firmware provenance does not identify the supplied J716s/26C5035r build")
    if firmware_provenance.get("output_sha256") != sha256(firmware / "apple/centauri/j716s-ftab.bin"):
        raise RuntimeError("supplied N1 firmware differs from its retained build provenance")
    for file in (firmware / "apple").rglob("*"):
        if "k114" in str(file).lower() or ("tpmtfw-" in file.name and file.name != "tpmtfw-j716s.bin"):
            raise RuntimeError("foreign-board firmware is not allowed: " + str(file))
    for name in ("m5-omarchy-j716s.sh", "keys", "policies"):
        if not (here / name).exists():
            raise RuntimeError("provisioning slice artifact missing: " + str(here / name))
    mesa_script = Path(args.mesa_build_script).resolve()
    mesa_workspace = Path(args.mesa_workspace).resolve()
    if not mesa_script.is_file() or mesa_workspace == root or root in mesa_workspace.parents:
        raise RuntimeError("Mesa needs a real build script and external guest workspace")
    run("bash", mesa_script, "--prepare", mesa_workspace)
    work.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = work / "image-build-stage.json"
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else None
    if args.resume_from:
        source_record = json.loads((work / "cache/alarm-download.json").read_text())
        source = work / "cache" / source_record["tarball_file"]
        if not source_record.get("signature_verified") or source_record.get("signing_key_fingerprint") != ALARM_BUILD_FINGERPRINT or sha256(source) != source_record["sha256"]:
            raise RuntimeError("staged source is not bound to the verified official ARM tarball")
        stage_receipt = validate_staged_root(root, kernel, firmware, release)
        before_mesa = set(checkpoint["before_mesa"] if checkpoint else stage_receipt["packages"])
        if args.resume_from == "mesa":
            if not checkpoint or checkpoint.get("stage") not in ("omarchy", "mesa"):
                raise RuntimeError("Mesa resume requires the signed package-stage checkpoint")
            receipt_hash, inventory_hash = validate_mesa_stage(root)
            if checkpoint.get("stage") == "mesa" and (receipt_hash != checkpoint["mesa_receipt_sha256"] or inventory_hash != checkpoint["mesa_inventory_sha256"]):
                raise RuntimeError("native Mesa checkpoint content differs")
            checkpoint.update(stage="mesa", mesa_receipt_sha256=receipt_hash, mesa_inventory_sha256=inventory_hash)
            save_json(checkpoint_path, checkpoint)
        if checkpoint and (checkpoint.get("source_sha256") != source_record["sha256"] or checkpoint.get("kernel_artifacts") != artifacts):
            raise RuntimeError("image-build checkpoint source/kernel differs")
        removed = checkpoint["removed_stock_packages"] if checkpoint else sorted({
            member.name.removeprefix("./").split("/")[4].rsplit("-", 2)[0]
            for member in tarfile.open(source) if member.name.removeprefix("./").startswith("var/lib/pacman/local/linux-")
            and len(member.name.removeprefix("./").split("/")) > 4
            and BOOT_DENY.fullmatch(member.name.removeprefix("./").split("/")[4].rsplit("-", 2)[0])})
        unmount_inherited_root(root)
    else:
        source, source_record = alarm_source(work / "cache", args.tarball, args.checksum)
        safe_archive(source)
        root.mkdir()
        # The upstream PAX archive uses LIBARCHIVE.xattr capability records.
        run("bsdtar", "--extract", "--file", source, "--directory", root,
            "--numeric-owner", "--preserve-permissions", "--acls", "--xattrs")
    metadata = root / "etc/j716s"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "kernel.release").write_text(release + "\n")
    library = root / "usr/local/lib/j716s"
    library.mkdir(parents=True, exist_ok=True)
    for name in ("m5-omarchy-j716s.sh", "j716s-bind-boot.py", "j716s-firstboot.sh", "j716s-recovery.sh"):
        install_file(here / name, library / name, 0o755)
    install_file(here / "candidate_boot.py", library / "candidate_boot.py", 0o644)
    for name in ("keys", "policies"):
        shutil.copytree(here / name, library / name, dirs_exist_ok=True)
    with chroot_mounts(root, work / "signed-package-cache"):
        if not args.resume_from:
            chroot(root, "pacman-key", "--init")
            chroot(root, "pacman-key", "--populate", "archlinuxarm")
            # Refuse any inherited package policy accepting unsigned packages.
            conf = root / "etc/pacman.conf"
            lines = conf.read_text().splitlines()
            conf.write_text("\n".join("SigLevel = Required DatabaseOptional" if re.match(r"^\s*SigLevel\s*=", line) else line for line in lines) + "\n")
            packages = capture("chroot", root, "pacman", "-Qq").splitlines()
            removed = sorted(name for name in packages if BOOT_DENY.fullmatch(name))
            if removed:
                chroot(root, "pacman", "-Rdd", "--noconfirm", "--noscriptlet", *removed)
            remove_path(root / "usr/lib/modules")
            remove_path(root / "usr/lib/firmware")
            for path in (root / "boot").iterdir():
                remove_path(path)
            chroot(root, "pacman", "-Syu", "--noconfirm", "--needed", "python", "sudo", "curl", "gnupg", "e2fsprogs", "util-linux", "libarchive", "archlinuxarm-keyring", "kmod")
            run("tar", "--extract", "--gzip", "--file", kernel / "modules.tar.gz", "--directory", root,
                "--numeric-owner", "--same-owner", "--same-permissions", "--acls", "--xattrs", "--xattrs-include=*", "--keep-directory-symlink")
            shutil.copytree(firmware / "apple", root / "usr/lib/firmware/apple", symlinks=True)
            for name in ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map", "Module.symvers", "kernel.release"):
                install_file(kernel / name, root / "usr/lib/j716s/kernel" / name)
            scrub_credentials(root)
            (metadata / "image-build").write_text("j716s-arm64-image-v1\n")
            chroot(root, "/usr/bin/bash", "/usr/local/lib/j716s/m5-omarchy-j716s.sh", "image", env={"J716S_IMAGE_BUILD": "1"})
            before_mesa = set(capture("chroot", root, "pacman", "-Qq").splitlines())
        if args.resume_from != "mesa":
            checkpoint = {"schema": 1, "stage": "omarchy", "source_sha256": source_record["sha256"],
                          "kernel_artifacts": artifacts, "before_mesa": sorted(before_mesa),
                          "removed_stock_packages": removed}
            save_json(checkpoint_path, checkpoint)
        build_packages = "base-devel meson ninja python python-mako python-yaml python-packaging python-ply llvm clang spirv-llvm-translator spirv-tools glslang pkgconf libdrm libglvnd wayland wayland-protocols xorgproto libx11 libxext libxcb libxshmfence libxxf86vm libxrandr expat zlib zstd elfutils".split()
        runtime_packages = "llvm-libs spirv-tools libdrm libglvnd wayland libx11 libxext libxcb libxshmfence libxxf86vm libxrandr expat zlib zstd libelf vulkan-icd-loader vulkan-tools python".split()
        package_env = {"J716S_IMAGE_BUILD": "1", "OMARCHY_ALLOW_DIRECT_PACMAN": "1"}
        if args.resume_from != "mesa":
            current_packages = set(capture("chroot", root, "pacman", "-Qq").splitlines())
            missing = sorted(set(build_packages + runtime_packages) - current_packages)
            if missing:
                chroot(root, "pacman", "-S", "--noconfirm", "--needed", *missing, env=package_env)
            chroot(root, "pacman", "-D", "--asexplicit", *runtime_packages, env=package_env)
            mesa_mount = root / "external-mesa-work"
            mesa_mount.mkdir(exist_ok=True)
            if any(mesa_mount.iterdir()):
                raise RuntimeError("external Mesa mountpoint contains unexpected image data")
            install_file(mesa_script, library / "build-mesa-j716s.sh", 0o755)
            run("mount", "--bind", mesa_workspace, mesa_mount)
            try:
                chroot(root, "/usr/bin/bash", "/usr/local/lib/j716s/build-mesa-j716s.sh", "--image-build", "--workspace", "/external-mesa-work", env=package_env)
            finally:
                run("umount", mesa_mount)
            mesa_mount.rmdir()
            remove_path(library / "build-mesa-j716s.sh")
        receipt_hash, inventory_hash = validate_mesa_stage(root)
        checkpoint.update(stage="mesa", mesa_receipt_sha256=receipt_hash, mesa_inventory_sha256=inventory_hash)
        save_json(checkpoint_path, checkpoint)
        current_packages = set(capture("chroot", root, "pacman", "-Qq").splitlines())
        build_only = sorted((set(build_packages) & current_packages) - set(runtime_packages) - before_mesa)
        if build_only:
            chroot(root, "pacman", "-Rns", "--noconfirm", *build_only, env=package_env)
        chroot(root, "python", "/usr/local/lib/j716s/policies/j716s-policy.py", "receipt", env=package_env)
        mesa_receipt_path = root / "opt/mesa-j716s/share/j716s/build-receipt.json"
        mesa_inventory_path = root / "opt/mesa-j716s/share/j716s/prefix-inventory.json"
        if not mesa_receipt_path.is_file() or not mesa_inventory_path.is_file():
            raise RuntimeError("native Mesa build receipt/inventory is missing")
        if args.resume_from == "mesa" and (sha256(mesa_receipt_path) != checkpoint["mesa_receipt_sha256"] or sha256(mesa_inventory_path) != checkpoint["mesa_inventory_sha256"]):
            raise RuntimeError("completed native Mesa checkpoint content differs")
        checkpoint.update(stage="mesa", mesa_receipt_sha256=sha256(mesa_receipt_path), mesa_inventory_sha256=sha256(mesa_inventory_path))
        save_json(checkpoint_path, checkpoint)
        (metadata / "image-build").unlink(missing_ok=True)
        scrub_credentials(root)
        installed_packages = capture("chroot", root, "pacman", "-Qq").splitlines()
        if any(BOOT_DENY.fullmatch(name) for name in installed_packages):
            raise RuntimeError("generic kernel/firmware package ownership returned during provisioning")
        if not (root / "var/lib/j716s/omarchy-image.json").is_file():
            raise RuntimeError("signed Omarchy image package receipt is missing")
        modules = root / "usr/lib/modules"
        if sorted(path.name for path in modules.iterdir()) != [release]:
            raise RuntimeError("installed modules do not belong exclusively to the supplied J716s kernel")
        for path in modules.glob("*/build"):
            remove_path(path)
        for path in modules.glob("*/source"):
            remove_path(path)
        chroot(root, "depmod", release)
        package_inventory = capture("chroot", root, "pacman", "-Q") + "\n"
        (metadata / "packages.txt").write_text(package_inventory)
        chroot(root, "systemctl", "disable", "sddm.service", "getty@tty1.service")
        chroot(root, "systemctl", "set-default", "multi-user.target")
    # Nothing is mounted below root during archive/export.
    # Recreated independently by offline firstboot from baked public keyrings.
    remove_path(root / "etc/pacman.d/gnupg")
    remove_path(root / "etc/resolv.conf")
    (root / "etc/resolv.conf").symlink_to("/run/systemd/resolve/stub-resolv.conf")
    for relative in ("tmp", "var/tmp", "run"):
        for path in (root / relative).iterdir():
            remove_path(path)
    # Pacman downloads, including signed package files, are retained as build
    # provenance outside the image; runtime provisioning does not need network.
    cache = root / "var/cache/pacman/pkg"
    if cache.exists():
        for path in cache.iterdir():
            remove_path(path)
        cache.chmod(0o755)
    if "filesystem_uuid" not in checkpoint:
        existing_fstab = (root / "etc/fstab").read_text()
        previous_uuid = re.search(r"^UUID=([0-9a-f-]{36}) / ext4", existing_fstab, re.MULTILINE)
        checkpoint["filesystem_uuid"] = str(uuid.UUID(previous_uuid[1])) if existing_fstab.startswith("# ESP is added only after deployment receipt validation.") and previous_uuid else str(uuid.uuid4())
        save_json(checkpoint_path, checkpoint)
    fsuuid = checkpoint["filesystem_uuid"]
    (root / "etc/fstab").write_text(f"# ESP is added only after deployment receipt validation.\nUUID={fsuuid} / ext4 rw,noatime,x-systemd.growfs 0 1\n")
    (root / "etc/hostname").write_text("j716s-omarchy\n")
    units = root / "etc/systemd/system"
    for name in ("j716s-boot-bind.service", "j716s-firstboot.service", "j716s-recovery.service"):
        install_file(here / name, units / name)
    wants = units / "multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    for name in ("j716s-boot-bind.service", "j716s-firstboot.service"):
        (wants / name).unlink(missing_ok=True)
        (wants / name).symlink_to("../" + name)
    dropin = units / "sddm.service.d/10-j716s-identity-gate.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Unit]\nRequires=j716s-boot-bind.service\nAfter=j716s-boot-bind.service j716s-firstboot.service\n\n[Service]\nExecStartPre=/usr/bin/test -f /var/lib/j716s/firstboot-complete\n")
    chroot(root, "/usr/local/libexec/j716s-session-env", "--greeter")
    graphics_helpers = {}
    for source_name, installed in (("j716s-gpu-preflight.py", "usr/local/bin/j716s-gpu-preflight"),
                                   ("j716s-session-env.py", "usr/local/libexec/j716s-session-env")):
        path = root / installed
        if sha256(path) != sha256(here / "graphics" / source_name):
            raise RuntimeError("installed graphics helper differs: " + source_name)
        graphics_helpers[installed] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
    firmware_records = tree_records(root / "usr/lib/firmware")
    module_records = tree_records(root / "usr/lib/modules")
    policy_records = tree_records(library)
    identity = {"schema": 1, "board": "j716s", "kernel_release": release,
                "firmware_build": "26C5035r", "filesystem_uuid": fsuuid,
                "source_sha256": source_record["sha256"],
                "kernel_build_manifest_sha256": kernel_hash, "loader_build_manifest_sha256": loader_hash,
                "graphics_helpers": graphics_helpers,
                "kernel_artifacts": artifacts, "firmware_tree_sha256": record_hash(firmware_records),
                "modules_tree_sha256": record_hash(module_records),
                "package_inventory_sha256": sha256(metadata / "packages.txt"),
                "omarchy_package_receipt_sha256": sha256(root / "var/lib/j716s/omarchy-image.json"),
                "mesa_build_receipt_sha256": sha256(mesa_receipt_path),
                "mesa_prefix_inventory_sha256": sha256(mesa_inventory_path),
                "provisioning_policy_sha256": record_hash(policy_records)}
    identity_path = root / "usr/share/j716s/image-identity.json"
    save_json(identity_path, identity)
    shutil.copyfile(metadata / "packages.txt", out.parent / "packages.txt")
    payload_size = int(capture("du", "-sx", "--block-size=1", root).split()[0])
    minimum = math.ceil((payload_size * 1.25 + 4 * GIB) / GIB)
    if args.size_gib < minimum:
        raise RuntimeError(f"complete baked root needs at least {minimum} GiB; requested {args.size_gib} GiB")
    size_bytes = args.size_gib * GIB
    with image.open("wb") as stream:
        stream.truncate(size_bytes)
    run("mke2fs", "-q", "-F", "-t", "ext4", "-b", "4096", "-L", "j716s-root", "-U", fsuuid,
        "-E", "root_owner=0:0,lazy_itable_init=0,lazy_journal_init=0", "-d", root, image, size_bytes // 4096)
    with (work / "e2fsck.log").open("w") as log:
        run("e2fsck", "-fn", image, stdout=log, stderr=subprocess.STDOUT)
    actual_uuid = capture("blkid", "-p", "-s", "UUID", "-o", "value", image)
    if actual_uuid != fsuuid:
        raise RuntimeError("created ext4 UUID is not the embedded UUID")
    raw_sha = sha256(image)
    with image.open("rb") as stream, out.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as compressed:
            shutil.copyfileobj(stream, compressed, 8 << 20)
    manifest = {"schema": 1, "kind": "j716s-omarchy-rootfs", "board": "j716s",
                "filesystem": "ext4", "filesystem_uuid": fsuuid, "block_size": 4096,
                "image_file": out.name, "sha256": sha256(out), "uncompressed_sha256": raw_sha,
                "image_size": size_bytes, "compressed_size": out.stat().st_size,
                "kernel_release": release, "firmware_build": "26C5035r",
                "kernel_build_manifest_sha256": kernel_hash, "loader_build_manifest_sha256": loader_hash,
                "graphics_helpers": graphics_helpers,
                "image_identity_sha256": sha256(identity_path), "image_identity": identity,
                "source": source_record, "kernel_artifacts": artifacts,
                "module_count": module_count, "modules": module_records,
                "firmware": firmware_records, "provisioning_policies": policy_records,
                "firmware_provenance": firmware_provenance,
                "removed_stock_packages": removed,
                "package_inventory_file": "packages.txt", "package_inventory_sha256": sha256(metadata / "packages.txt"),
                "omarchy_package_receipt": json.loads((root / "var/lib/j716s/omarchy-image.json").read_text()),
                "mesa_build_receipt": json.loads(mesa_receipt_path.read_text()),
                "mesa_prefix_inventory_sha256": sha256(mesa_inventory_path),
                "fsck": {"command": "e2fsck -fn", "exit_status": 0, "log_sha256": sha256(work / "e2fsck.log")},
                "metadata_preservation": "native root bsdtar extraction with numeric owners/ACLs/xattrs; native e2fsprogs directory population preserves ownership, modes, hardlinks and extended attributes",
                "credentials": "no alarm/default credentials; root locked; SSH host/user keys removed; sshd masked; password wheel sudo",
                "boot_acknowledgement": "standard supplied-kernel mechanism only; no userspace register writes; hardware execution not validated",
                "hardware_boot_tested": False}
    save_json(out.parent / "root-manifest.json", manifest)
    print(json.dumps({"image": str(out), "manifest": str(out.parent / "root-manifest.json"),
                      "filesystem_uuid": fsuuid, "size_bytes": size_bytes}, indent=2))


if __name__ == "__main__":
    main()
