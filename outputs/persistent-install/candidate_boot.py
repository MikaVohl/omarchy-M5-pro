"""Read-only authorization of receipt-bound trial and persistent open-WiFi boots.

Schema 1 (kind j716s-open-wifi-trial) binds trial_id, baseline_identity_sha256,
baseline_deployment_sha256, kernel_release, the full build_manifest, files and
modules. Inventory records are {path, size, sha256}; boot paths are ESP-relative,
module paths are relative to usr/lib/modules/<kernel_release>.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

BASELINE_RELEASE = "7.3.0-j716s-8ab10100a4c1"
CANDIDATE_RELEASE = BASELINE_RELEASE + "-open-wifi"
KERNEL_COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
PATCH_SHA256 = "93fe93402d2631475792635b1c3e51b187a94fe15b8518de3fcc09dd84c3f75c"
TOKEN = "j716s.candidate"
BOOT_FILES = frozenset({"aurora/open-wifi.bin", "aurora/open-wifi/Image.gz",
                        "aurora/open-wifi/t6050-j716s.dtb",
                        "aurora/open-wifi/initramfs.cpio.gz"})
BUILD_ARTIFACTS = frozenset({"Image", "Image.gz", "t6050-j716s.dtb", ".config",
                             "System.map", "Module.symvers", "kernel.release",
                             "modules.tar.gz"})


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            value.update(block)
    return value.hexdigest()


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value,
            "unsafe candidate inventory path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == value
            and value != ".", "unsafe candidate inventory path: " + value)
    return path


def safe_path(root, relative, directory=False):
    path = root
    parts = relative_path(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), "symlink in candidate path: " + str(path))
        is_directory = index < len(parts) - 1 or directory
        require(stat.S_ISDIR(mode) if is_directory else stat.S_ISREG(mode),
                "wrong candidate file type: " + str(path))
    return path


def inventory(records):
    require(isinstance(records, list), "candidate inventory must be a list")
    result = {}
    for entry in records:
        require(isinstance(entry, dict) and set(entry) == {"path", "size", "sha256"},
                "unsupported candidate inventory record")
        relative_path(entry["path"])
        require(type(entry["size"]) is int and entry["size"] >= 0 and sha256(entry["sha256"]),
                "invalid candidate inventory size or digest")
        require(entry["path"] not in result, "duplicate candidate inventory path")
        result[entry["path"]] = entry
    return result


def verify_file(root, entry):
    path = safe_path(root, entry["path"])
    require(path.stat().st_size == entry["size"] and digest(path) == entry["sha256"],
            "candidate file differs: " + entry["path"])


def verify_candidate(root, baseline_release, identity, deployment):
    """Validate stored trial data without requiring a currently running trial.

    Return the parsed receipt. root is the mounted root; identity and deployment
    are its parsed baseline JSON objects. Validation never mutates state.
    """
    require(baseline_release == BASELINE_RELEASE, "unsupported candidate baseline kernel")
    root = Path(root).resolve()
    receipt_path = safe_path(root, "etc/j716s/open-wifi.json")
    metadata = receipt_path.stat()
    require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
            "candidate receipt must be root-owned and not group/world writable")
    receipt = json.loads(receipt_path.read_text())
    require(isinstance(receipt, dict) and set(receipt) == {
        "schema", "kind", "trial_id", "baseline_identity_sha256", "baseline_deployment_sha256",
        "kernel_release", "build_manifest", "files", "modules"}, "unsupported candidate receipt fields")
    require(type(receipt["schema"]) is int and receipt["schema"] == 1
            and receipt["kind"] == "j716s-open-wifi-trial", "unsupported candidate receipt")
    require(sha256(receipt["trial_id"]), "invalid candidate trial ID")
    require(receipt["kernel_release"] == CANDIDATE_RELEASE, "candidate receipt kernel differs")
    for relative, key, expected in (
            ("usr/share/j716s/image-identity.json", "baseline_identity_sha256", identity),
            ("boot/aurora/deployment.json", "baseline_deployment_sha256", deployment)):
        path = safe_path(root, relative)
        require(sha256(receipt[key]) and digest(path) == receipt[key], "candidate baseline digest differs: " + key)
        require(json.loads(path.read_text()) == expected, "candidate baseline object differs: " + key)
    require(identity["kernel_release"] == deployment["kernel_release"] == baseline_release,
            "candidate baseline kernel differs")
    build = receipt["build_manifest"]
    require(isinstance(build, dict) and set(build) == {
        "schema", "commit", "kernel_release", "artifacts", "variant", "qualification", "patches"},
        "candidate must embed the full build manifest")
    require(type(build["schema"]) is int and build["schema"] == 1
            and build["commit"] == KERNEL_COMMIT
            and build["kernel_release"] == CANDIDATE_RELEASE
            and build["variant"] == "open-wifi"
            and build["qualification"] == "unqualified-experimental"
            and build["patches"] == {"kernel-j716s-open-wifi.patch": PATCH_SHA256},
            "candidate build pins, variant, qualification or patch differ")
    artifacts = build["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) == BUILD_ARTIFACTS,
            "candidate build artifact inventory differs")
    for artifact in artifacts.values():
        require(isinstance(artifact, dict) and set(artifact) == {"bytes", "sha256"}
                and type(artifact["bytes"]) is int and artifact["bytes"] >= 0
                and sha256(artifact["sha256"]), "invalid candidate build artifact")
    for name in (".config", "t6050-j716s.dtb"):
        baseline = identity["kernel_artifacts"][name]
        require(artifacts[name]["sha256"] == baseline["sha256"]
                and artifacts[name]["bytes"] == baseline["size_bytes"],
                "candidate changed baseline build artifact: " + name)
    files = inventory(receipt["files"])
    require(set(files) == BOOT_FILES, "candidate boot inventory differs")
    image = files["aurora/open-wifi/Image.gz"]
    require(image["sha256"] == artifacts["Image.gz"]["sha256"]
            and image["size"] == artifacts["Image.gz"]["bytes"], "candidate kernel build binding differs")
    dependencies = deployment["payload_files"]
    for name in ("t6050-j716s.dtb", "initramfs.cpio.gz"):
        baseline = [entry for entry in dependencies if entry["path"] == "aurora/root/" + name]
        require(len(baseline) == 1, "baseline candidate dependency is missing or duplicated")
        entry = files["aurora/open-wifi/" + name]
        require(entry["sha256"] == baseline[0]["sha256"] and entry["size"] == baseline[0]["size"],
                "candidate changed baseline boot dependency: " + name)
    for entry in files.values():
        verify_file(root / "boot", entry)
    modules = inventory(receipt["modules"])
    require(modules, "candidate module inventory is empty")
    module_root = safe_path(root, "usr/lib/modules/" + CANDIDATE_RELEASE, directory=True)

    def walk_error(error):
        raise error

    actual = set()
    for directory, directories, filenames in os.walk(module_root, followlinks=False, onerror=walk_error):
        for name in directories:
            path = Path(directory) / name
            require(stat.S_ISDIR(path.lstat().st_mode), "unsafe candidate module directory: " + str(path))
        for name in filenames:
            path = Path(directory) / name
            require(stat.S_ISREG(path.lstat().st_mode), "unsafe candidate module file: " + str(path))
            actual.add(path.relative_to(module_root).as_posix())
    require(actual == set(modules), "candidate module inventory differs")
    for entry in modules.values():
        verify_file(module_root, entry)
    return receipt


def _validate_persistent_selection(root, deployment, receipt, selected_sha256=None):
    path = safe_path(root, "etc/j716s/open-wifi-default.json")
    metadata = path.stat()
    require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
            "persistent receipt must be root-owned and not group/world writable")
    persistent = json.loads(path.read_text())
    require(isinstance(persistent, dict) and set(persistent) == {
        "schema", "kind", "kernel_release", "candidate_receipt_sha256",
        "baseline_payload_sha256", "selected_payload_sha256"},
        "unsupported persistent receipt fields")
    require(type(persistent["schema"]) is int and persistent["schema"] == 1
            and persistent["kind"] == "j716s-open-wifi-default"
            and persistent["kernel_release"] == CANDIDATE_RELEASE,
            "unsupported persistent receipt")
    candidate_payload = next(entry["sha256"] for entry in receipt["files"]
                             if entry["path"] == "aurora/open-wifi.bin")
    for key, expected in (
            ("candidate_receipt_sha256", digest(safe_path(root, "etc/j716s/open-wifi.json"))),
            ("baseline_payload_sha256", deployment["root_payload_sha256"]),
            ("selected_payload_sha256", candidate_payload)):
        require(sha256(persistent[key]) and persistent[key] == expected,
                "persistent receipt binding differs: " + key)
    if selected_sha256 is None:
        selected_sha256 = digest(safe_path(root, "boot/aurora/boot.bin"))
    require(selected_sha256 == candidate_payload,
            "selected boot payload differs from the persistent candidate")


def validate_boot_selection(root, baseline_release, identity, deployment):
    """Authorize the selected baseline payload or the explicitly bound candidate."""
    root = Path(root).resolve()
    selected_sha256 = digest(safe_path(root, "boot/aurora/boot.bin"))
    if selected_sha256 == deployment["root_payload_sha256"]:
        return
    receipt = verify_candidate(root, baseline_release, identity, deployment)
    _validate_persistent_selection(root, deployment, receipt, selected_sha256)


def validate_running_kernel(root, baseline_release, identity, deployment, cmdline,
                            running_release=None):
    """Return None only for the baseline or a fully bound open-WiFi boot.

    cmdline is the guard's key/value mapping. All failures raise RuntimeError,
    ValueError, KeyError or OSError; no state is mutated.
    """
    running_release = os.uname().release if running_release is None else running_release
    if running_release == baseline_release and TOKEN not in cmdline:
        return
    require(running_release == CANDIDATE_RELEASE,
            "running kernel is neither the baseline nor the exact open-WiFi candidate")
    require(TOKEN in cmdline and sha256(cmdline[TOKEN]), "candidate command-line trial ID is missing")
    receipt = verify_candidate(root, baseline_release, identity, deployment)
    require(receipt["trial_id"] == cmdline[TOKEN], "candidate receipt trial ID differs")
    root = Path(root).resolve()
    trial = root / "sys/firmware/devicetree/base/chosen/aurora,trial"
    if not trial.exists():
        trial = root / "proc/device-tree/chosen/aurora,trial"
    if trial.exists():
        require(trial.read_bytes().strip(b"\0") == b"aurora/open-wifi.bin",
                "candidate is not the one-shot open-WiFi trial")
    else:
        _validate_persistent_selection(root, deployment, receipt)
