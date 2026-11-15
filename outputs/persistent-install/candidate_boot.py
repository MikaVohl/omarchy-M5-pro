"""Read-only authorization of pinned J716s trial and persistent kernel variants.

Schema 1 binds trial_id, baseline identity/deployment digests, the full build
manifest, ESP files and modules. SEP discovery permits a build-bound DTB only in
a guarded one-shot trial; bringup binds the exact Bluetooth config delta.
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
BRINGUP_RELEASE = BASELINE_RELEASE + "-bringup"
BLUETOOTH_PATCH_SHA256 = "d4ec8a90431108c3887eb2dd99b29d11aaeca78b92fcaf2a5dccf32d9ff2229b"
SEP_PROBE_RELEASE = BASELINE_RELEASE + "-sep-probe"
SEP_PROBE_PATCH_SHA256 = "dee143ac4c83be7f96157a36ada4329f38c032e9c3441298f690f4f8f1abe8b4"
SEP_PROBE_PARAMETERS = {
    "apple_sep.xart_writes": "0", "apple_sep.provision_keybag": "0",
    "apple_sep.xart_guard": "2", "apple_sep.trusted_keys": "0",
    "apple_sep.filevault_api": "0", "apple_sep.keystore_only": "1",
}
SEP_SENSOR_RELEASE = BASELINE_RELEASE + "-sep-sensor"
SEP_SENSOR_PARAMETERS = {**SEP_PROBE_PARAMETERS, "apple_sep.sensor_auth_probe": "1"}
ONE_SHOT_PARAMETERS = {"sep-probe": SEP_PROBE_PARAMETERS, "sep-sensor": SEP_SENSOR_PARAMETERS}
SENSOR_MATERIAL_PREFIX = "var/lib/j716s/sep-sensor-material"
SENSOR_PROVENANCE = SENSOR_MATERIAL_PREFIX + "/sensor-firmware-provenance.json"
SENSOR_FIRMWARE_NAMES = frozenset({"apple/mesacal-j716s.bin", "apple/msrk-j716s.bin"})
SENSOR_MATERIAL_FILES = frozenset({SENSOR_PROVENANCE,
    *(SENSOR_MATERIAL_PREFIX + "/" + name for name in SENSOR_FIRMWARE_NAMES),
    *("usr/lib/firmware/" + name for name in SENSOR_FIRMWARE_NAMES)})
BLUETOOTH_CONFIG = frozenset({"CONFIG_BT_BREDR", "CONFIG_BT_LE", "CONFIG_BT_RFCOMM",
                            "CONFIG_BT_BNEP", "CONFIG_INPUT_MISC", "CONFIG_INPUT_UINPUT"})
CONFIG_DELTA = BLUETOOTH_CONFIG | {"CONFIG_BT_LE_L2CAP_ECRED"}
TOUCHID_RELEASE = BASELINE_RELEASE + "-touchid"
TOUCHID_VARIANTS = frozenset({"touchid", "touchid-sco", "touchid-sco-spk"})
SEP_SENSOR_PATCH_SHA256 = "c8eba0627fdfdd1912882eed615203c3a0accd375774db1dd6c8d30a4958abf5"
# Variants that carry the Bluetooth config delta (and its two configs on the ESP).
CONFIG_VARIANTS = frozenset({"bringup", *TOUCHID_VARIANTS})
# Variants whose DTB is their own build's, bound to the build manifest.
BUILD_DTB_VARIANTS = frozenset({"sep-probe", "sep-sensor", *TOUCHID_VARIANTS})
VARIANTS = {
    "open-wifi": {"release": CANDIDATE_RELEASE, "patches": {"kernel-j716s-open-wifi.patch": PATCH_SHA256}},
    "bringup": {"release": BRINGUP_RELEASE, "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-bluetooth.patch": BLUETOOTH_PATCH_SHA256,
        "kernel-g17-unblamed-recovery-diagnostics.patch": "07cb60b0ae566083b5300e9d824e847f6f6d467380ac68b9c14c051a792c16ce"}},
    "sep-probe": {"release": SEP_PROBE_RELEASE, "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-sep-probe.patch": SEP_PROBE_PATCH_SHA256}},
    "sep-sensor": {"release": SEP_SENSOR_RELEASE, "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-sep-probe.patch": SEP_PROBE_PATCH_SHA256,
        "kernel-j716s-sep-sensor.patch": SEP_SENSOR_PATCH_SHA256}},
    "touchid": {"release": TOUCHID_RELEASE, "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-bluetooth.patch": BLUETOOTH_PATCH_SHA256,
        "kernel-g17-unblamed-recovery-diagnostics.patch": "07cb60b0ae566083b5300e9d824e847f6f6d467380ac68b9c14c051a792c16ce",
        "kernel-j716s-sep-probe.patch": SEP_PROBE_PATCH_SHA256,
        "kernel-j716s-sep-sensor.patch": SEP_SENSOR_PATCH_SHA256,
        "kernel-j716s-touchid.patch": "43d0bd360349b123270766c3551983121c453be10581cbe8ef50d610e0f29486"}},
    "touchid-sco": {"release": BASELINE_RELEASE + "-touchid-sco", "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-bluetooth.patch": BLUETOOTH_PATCH_SHA256,
        "kernel-g17-unblamed-recovery-diagnostics.patch": "07cb60b0ae566083b5300e9d824e847f6f6d467380ac68b9c14c051a792c16ce",
        "kernel-j716s-sep-probe.patch": SEP_PROBE_PATCH_SHA256,
        "kernel-j716s-sep-sensor.patch": SEP_SENSOR_PATCH_SHA256,
        "kernel-j716s-touchid.patch": "43d0bd360349b123270766c3551983121c453be10581cbe8ef50d610e0f29486",
        "kernel-j716s-bluetooth-sco.patch": "573c10d0d5212c738823d3423ea0399637ff4227bfa4fa8fbfafca81c64cb87d",
        "kernel-j716s-prores-dma-ownership.patch": "be5b22ad12046c8013182c21d58fc5ded9f481d71a53401c05cd60a3599f1483"}},
    "touchid-sco-spk": {"release": BASELINE_RELEASE + "-touchid-sco-spk", "patches": {
        "kernel-j716s-open-wifi.patch": PATCH_SHA256,
        "kernel-j716s-bluetooth.patch": BLUETOOTH_PATCH_SHA256,
        "kernel-g17-unblamed-recovery-diagnostics.patch": "07cb60b0ae566083b5300e9d824e847f6f6d467380ac68b9c14c051a792c16ce",
        "kernel-j716s-sep-probe.patch": SEP_PROBE_PATCH_SHA256,
        "kernel-j716s-sep-sensor.patch": SEP_SENSOR_PATCH_SHA256,
        "kernel-j716s-touchid.patch": "43d0bd360349b123270766c3551983121c453be10581cbe8ef50d610e0f29486",
        "kernel-j716s-bluetooth-sco.patch": "573c10d0d5212c738823d3423ea0399637ff4227bfa4fa8fbfafca81c64cb87d",
        "kernel-j716s-prores-dma-ownership.patch": "be5b22ad12046c8013182c21d58fc5ded9f481d71a53401c05cd60a3599f1483",
        "kernel-j716s-v2-operating-ceiling.patch": "8f21f0408e4bf35a9cfa950667685427c86b728d8d8294e21e21f78f254f5846"}},
}


def variant_paths(variant):
    require(variant in VARIANTS, "unsupported kernel variant")
    prefix = "aurora/" + variant
    files = {prefix + ".bin", *(prefix + "/" + name for name in
             ("Image.gz", "t6050-j716s.dtb", "initramfs.cpio.gz"))}
    if variant in CONFIG_VARIANTS:
        files.update({prefix + "/.config", prefix + "/baseline.config"})
    if variant == "sep-sensor":
        files.remove(prefix + "/initramfs.cpio.gz")
        files.add("aurora/root/initramfs.cpio.gz")
    return prefix, files


def kernel_config(text):
    values = {}
    for line in text.splitlines():
        disabled = re.fullmatch(r"# (CONFIG_\w+) is not set", line)
        enabled = re.fullmatch(r"(CONFIG_\w+)=(.+)", line)
        if not (disabled or enabled):
            require(not line.startswith("CONFIG_"), "malformed kernel config")
            continue
        key = (disabled or enabled)[1]
        require(key not in values, "duplicate kernel config: " + key)
        values[key] = "n" if disabled else enabled[2]
    return values


def validate_bluetooth_config(baseline_text, candidate_text):
    baseline, candidate = kernel_config(baseline_text), kernel_config(candidate_text)
    require(baseline and candidate, "empty kernel config")
    changed = {key for key in baseline.keys() | candidate.keys()
               if baseline.get(key, "n") != candidate.get(key, "n")}
    require(changed <= CONFIG_DELTA, "bringup changed unrelated kernel config: " + ", ".join(sorted(changed - CONFIG_DELTA)))
    require(all(candidate.get(key) == "y" for key in BLUETOOTH_CONFIG), "Bluetooth capabilities must be built in")


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


def verify_sensor_material(root, binding):
    require(isinstance(binding, dict) and set(binding) == {"provenance_sha256", "unit_serial_sha256", "files", "current_restore_build_verified"},
            "unsupported sensor material binding")
    require(binding["current_restore_build_verified"] is False,
            "sensor material profile matching is not current restore-build certification")
    require(sha256(binding["provenance_sha256"]) and sha256(binding["unit_serial_sha256"]), "invalid sensor material digest")
    serial = (root / "sys/firmware/devicetree/base/serial-number").read_bytes().rstrip(b"\0")
    require(serial and hashlib.sha256(serial).hexdigest() == binding["unit_serial_sha256"], "sensor material is from another unit")
    files = inventory(binding["files"])
    require(set(files) == SENSOR_MATERIAL_FILES, "sensor material inventory differs")
    require(files[SENSOR_PROVENANCE]["sha256"] == binding["provenance_sha256"], "sensor provenance binding differs")
    require(files["usr/lib/firmware/apple/msrk-j716s.bin"]["size"] <= 0x4b000,
            "sensor signed-root material exceeds the pinned SBIO transaction limit")
    for entry in files.values():
        path = safe_path(root, entry["path"])
        metadata = path.stat()
        require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0 and metadata.st_nlink == 1,
                "sensor material must be root-owned, single-link and not writable by others")
        require(entry["size"] > 0, "empty sensor material")
        verify_file(root, entry)
    for name in SENSOR_FIRMWARE_NAMES:
        package, installed = files[SENSOR_MATERIAL_PREFIX + "/" + name], files["usr/lib/firmware/" + name]
        require(package["size"] == installed["size"] and package["sha256"] == installed["sha256"],
                "installed sensor firmware differs from admitted private package")


def verify_candidate(root, baseline_release, identity, deployment, variant="open-wifi"):
    """Validate stored trial data without requiring a currently running trial.

    Return the parsed receipt. root is the mounted root; identity and deployment
    are its parsed baseline JSON objects. Validation never mutates state.
    """
    require(baseline_release == BASELINE_RELEASE, "unsupported candidate baseline kernel")
    prefix, boot_files = variant_paths(variant)
    pins = VARIANTS[variant]
    root = Path(root).resolve()
    receipt_path = safe_path(root, "etc/j716s/" + variant + ".json")
    metadata = receipt_path.stat()
    require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
            "candidate receipt must be root-owned and not group/world writable")
    receipt = json.loads(receipt_path.read_text())
    fields = {"schema", "kind", "trial_id", "baseline_identity_sha256", "baseline_deployment_sha256",
              "kernel_release", "build_manifest", "files", "modules"}
    if variant == "sep-sensor":
        fields.add("sensor_firmware")
    require(isinstance(receipt, dict) and set(receipt) == fields, "unsupported candidate receipt fields")
    require(type(receipt["schema"]) is int and receipt["schema"] == 1
            and receipt["kind"] == "j716s-" + variant + "-trial", "unsupported candidate receipt")
    require(sha256(receipt["trial_id"]), "invalid candidate trial ID")
    require(receipt["kernel_release"] == pins["release"], "candidate receipt kernel differs")
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
            and build["kernel_release"] == pins["release"]
            and build["variant"] == variant
            and build["qualification"] == "unqualified-experimental"
            and build["patches"] == pins["patches"],
            "candidate build pins, variant, qualification or patch differ")
    artifacts = build["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) == BUILD_ARTIFACTS,
            "candidate build artifact inventory differs")
    for artifact in artifacts.values():
        require(isinstance(artifact, dict) and set(artifact) == {"bytes", "sha256"}
                and type(artifact["bytes"]) is int and artifact["bytes"] >= 0
                and sha256(artifact["sha256"]), "invalid candidate build artifact")
    baseline_artifacts = {"bringup": ("t6050-j716s.dtb",),
                          **{name: () for name in TOUCHID_VARIANTS},
                          **{name: (".config",) for name in ONE_SHOT_PARAMETERS}}
    for name in baseline_artifacts.get(variant, (".config", "t6050-j716s.dtb")):
        baseline = identity["kernel_artifacts"][name]
        require(artifacts[name]["sha256"] == baseline["sha256"]
                and artifacts[name]["bytes"] == baseline["size_bytes"],
                "candidate changed baseline build artifact: " + name)
    files = inventory(receipt["files"])
    require(set(files) == boot_files, "candidate boot inventory differs")
    image = files[prefix + "/Image.gz"]
    require(image["sha256"] == artifacts["Image.gz"]["sha256"]
            and image["size"] == artifacts["Image.gz"]["bytes"], "candidate kernel build binding differs")
    dependencies = deployment["payload_files"]
    for name in ("t6050-j716s.dtb", "initramfs.cpio.gz"):
        baseline = [entry for entry in dependencies if entry["path"] == "aurora/root/" + name]
        require(len(baseline) == 1, "baseline candidate dependency is missing or duplicated")
        entry = files["aurora/root/" + name if variant == "sep-sensor" and name == "initramfs.cpio.gz" else prefix + "/" + name]
        if variant in BUILD_DTB_VARIANTS and name == "t6050-j716s.dtb":
            require(entry["sha256"] == artifacts[name]["sha256"]
                    and entry["size"] == artifacts[name]["bytes"],
                    "candidate DTB build binding differs")
            continue
        require(entry["sha256"] == baseline[0]["sha256"] and entry["size"] == baseline[0]["size"],
                "candidate changed baseline boot dependency: " + name)
    for entry in files.values():
        verify_file(root / "boot", entry)
    if variant == "sep-sensor":
        verify_sensor_material(root, receipt["sensor_firmware"])
    modules = inventory(receipt["modules"])
    if variant in CONFIG_VARIANTS:
        for name, expected in ((".config", artifacts[".config"]),
                               ("baseline.config", {"bytes": identity["kernel_artifacts"][".config"]["size_bytes"],
                                                    "sha256": identity["kernel_artifacts"][".config"]["sha256"]})):
            entry = files[prefix + "/" + name]
            require(entry["size"] == expected["bytes"] and entry["sha256"] == expected["sha256"], "config build binding differs")
        validate_bluetooth_config(safe_path(root / "boot", prefix + "/baseline.config").read_text(),
                                  safe_path(root / "boot", prefix + "/.config").read_text())
    require(modules, "candidate module inventory is empty")
    module_root = safe_path(root, "usr/lib/modules/" + pins["release"], directory=True)

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
    variant = receipt["build_manifest"]["variant"]
    require(variant not in ONE_SHOT_PARAMETERS, "SEP trial has no persistent authorization")
    prefix, _ = variant_paths(variant)
    path = safe_path(root, "etc/j716s/" + variant + "-default.json")
    metadata = path.stat()
    require(metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0,
            "persistent receipt must be root-owned and not group/world writable")
    persistent = json.loads(path.read_text())
    require(isinstance(persistent, dict) and set(persistent) == {
        "schema", "kind", "kernel_release", "candidate_receipt_sha256",
        "baseline_payload_sha256", "selected_payload_sha256"},
        "unsupported persistent receipt fields")
    require(type(persistent["schema"]) is int and persistent["schema"] == 1
            and persistent["kind"] == "j716s-" + variant + "-default"
            and persistent["kernel_release"] == VARIANTS[variant]["release"],
            "unsupported persistent receipt")
    candidate_payload = next(entry["sha256"] for entry in receipt["files"]
                             if entry["path"] == prefix + ".bin")
    for key, expected in (
            ("candidate_receipt_sha256", digest(safe_path(root, "etc/j716s/" + variant + ".json"))),
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
    require(not any((root / ("etc/j716s/" + variant + "-default.json")).exists() for variant in ONE_SHOT_PARAMETERS),
            "SEP trial has no persistent authorization")
    selected_sha256 = digest(safe_path(root, "boot/aurora/boot.bin"))
    if selected_sha256 == deployment["root_payload_sha256"]:
        return
    matches = []
    for variant in VARIANTS:
        path = root / ("etc/j716s/" + variant + "-default.json")
        if path.exists():
            persistent = json.loads(safe_path(root, str(path.relative_to(root))).read_text())
            if persistent.get("selected_payload_sha256") == selected_sha256:
                matches.append(variant)
    require(len(matches) == 1, "selected payload has no unique pinned authorization")
    receipt = verify_candidate(root, baseline_release, identity, deployment, matches[0])
    _validate_persistent_selection(root, deployment, receipt, selected_sha256)


def validate_running_kernel(root, baseline_release, identity, deployment, cmdline,
                            running_release=None):
    """Return None only for the baseline or a fully bound pinned variant.

    cmdline is the guard's key/value mapping. All failures raise RuntimeError,
    ValueError, KeyError or OSError; no state is mutated.
    """
    running_release = os.uname().release if running_release is None else running_release
    if running_release == baseline_release and TOKEN not in cmdline:
        return
    matches = [variant for variant, pins in VARIANTS.items() if pins["release"] == running_release]
    require(len(matches) == 1, "running kernel is neither baseline nor an exact pinned candidate")
    variant = matches[0]
    if variant in ONE_SHOT_PARAMETERS:
        require(all(cmdline.get(key) == value for key, value in ONE_SHOT_PARAMETERS[variant].items()),
                "SEP trial requires safe command-line flags")
    if variant == "sep-sensor":
        require(not {"apple_sep.sensor_spi_mode", "apple_sep.sensor_test_fault"}.intersection(cmdline),
                "sensor trial forbids SPI-mode overrides and fault injection")
    prefix, _ = variant_paths(variant)
    require(TOKEN in cmdline and sha256(cmdline[TOKEN]), "candidate command-line trial ID is missing")
    receipt = verify_candidate(root, baseline_release, identity, deployment, variant)
    require(receipt["trial_id"] == cmdline[TOKEN], "candidate receipt trial ID differs")
    root = Path(root).resolve()
    if variant in ONE_SHOT_PARAMETERS:
        require(not (root / ("etc/j716s/" + variant + "-default.json")).exists(),
                "SEP trial has no persistent authorization")
    trial = root / "sys/firmware/devicetree/base/chosen/aurora,trial"
    if not trial.exists():
        trial = root / "proc/device-tree/chosen/aurora,trial"
    if trial.exists():
        require(trial.read_bytes().strip(b"\0") == (prefix + ".bin").encode(),
                "candidate does not match its one-shot trial")
    else:
        require(variant not in ONE_SHOT_PARAMETERS, "SEP trial requires a matching one-shot trial")
        # The running kernel's own default stays bound; a newer selection (a deployment
        # or rollback awaiting reboot) must itself be authorized.
        running_payload = next(entry["sha256"] for entry in receipt["files"] if entry["path"] == prefix + ".bin")
        _validate_persistent_selection(root, deployment, receipt, running_payload)
        if digest(safe_path(root, "boot/aurora/boot.bin")) != running_payload:
            validate_boot_selection(root, baseline_release, identity, deployment)
