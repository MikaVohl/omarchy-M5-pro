#!/usr/bin/env python3
"""Build/install a pinned J716s speaker stack without starting it.

prepare builds the pinned source and checks native voicing admission offline.
install copies only new files; existing distribution files are never replaced.
rollback requires all speaker units stopped and removes only unchanged files
from its externally anchored receipt. Neither operation enables or starts audio.

Before a dual-kernel upgrade marker exists, run provisioning directly; its
service's gate prepare intentionally skips. Run the silent hardware check with
--force --switch-interface, then activate and verify. The temporary lease does
not write the marker and returns to the legacy floor after its owner closes.
Only the upstream upgrade's successful run/verify may persist the switch and
start the supervisor. Verify the live zero-only graph before nonzero audio.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("speaker_reference", ROOT / "scripts/capture-speaker-reference.py")
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)
SOURCE = ROOT / "work/build/speaker-bringup" / ("speakerguardd-" + ref.REV)
RELEASE_PATCH = ROOT / "patches/speakerguardd-native-release-attestation.patch"
RELEASE_PATCH_SHA = "dcc5bc9ca5cd5b3b868b63f86097fa579774b027c2af88e8b99d639370d87d5b"
RELEASE_TEST = ROOT / "tests/speaker-native-release-attestation.rs"
RELEASE_TEST_SHA = "d08ba7ca89a4fd424cb79552492ba206ffb3133c263c0a7cf089580489b0f099"
UPGRADE_PATCH = ROOT / "patches/speakerguardd-upgrade-v2-unit.patch"
UPGRADE_PATCH_SHA = "803c0f0bd2259592e861678c455281190e05a54fc735f470776423bf35263004"
UPGRADE_TEST = ROOT / "tests/speaker-upgrade-unit-detection.py"
UPGRADE_TEST_SHA = "ef7a878937c0445deb85c40c3bac749c91cb2edb7678db5a12f175b42e3d7f58"
ASSET_PATCH = ROOT / "patches/speakerguardd-upgrade-buzzkill-asset.patch"
ASSET_PATCH_SHA = "f697166f4d68f0de43c4f739946935aa24eb14b34c40346e773f45fc331440f8"
ASSET_TEST = ROOT / "tests/speaker-upgrade-buzzkill-asset.py"
ASSET_TEST_SHA = "c964a62cb003c66846abc732fc85c6ab6ff174ffccd884ddd166e759c313eec4"
MANIFEST = "speaker-runtime.json"
RECEIPT = "speaker-runtime-install.json"
BUNDLE = "var/lib/speakerguardd/speaker-tuning.tar.gz"
SYSTEM_UNITS = ("speakerguardd.service", "speakerguardd-provision.service", "speakerguardd-selfcheck.service",
                "speakerguardd-switchtest.service", "speakerguardd-seat.path", "speakerguardd-seat.service",
                "speakerguardd-sleep.service")
USER_UNITS = ("speakerguardd-graph.service", "speakerguardd-session.service")


def source_identity(source, archive):
    patch = ref.PATCH.read_bytes()
    if archive.is_symlink() or not archive.is_file() or hashlib.sha256(patch).hexdigest() != ref.PATCH_SHA:
        raise ValueError("source archive/transport patch differs")
    with archive.open("rb") as stream, tempfile.TemporaryDirectory() as tmp:
        expected = Path(tmp)
        prefix = "speakerguardd-" + ref.REV
        seen = set()
        if hashlib.file_digest(stream, "sha256").hexdigest() != ref.ARCHIVE_SHA:
            raise ValueError("source archive differs")
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix
                        or not (member.isdir() or member.isfile()) or member.name in seen):
                    raise ValueError("unsafe/duplicate speaker source member")
                seen.add(member.name)
                member.name = str(PurePosixPath(*path.parts[1:]))
                if member.name != ".":
                    tar.extract(member, expected, filter="data")
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], input=patch,
                       cwd=expected, check=True, timeout=30)
        files = ref.inventory(expected)
        for name, sha in files.items():
            if ref.digest(safe_path(source, name)) != sha:
                raise ValueError("pinned source differs: " + name)
        # Cargo output is not source. Unknown source additions may affect macros,
        # build scripts or feature selection, so do not silently ignore them.
        extra = {p.relative_to(source).as_posix() for p in source.rglob("*")
                 if p.is_file() and p.relative_to(source).parts[0] != "target"} - files.keys()
        if extra:
            raise ValueError("unbound source files: " + ", ".join(sorted(extra)))
        return files


def patched_source(source, destination, files):
    """Preserve the original source/capture; build a separately bound candidate."""
    patch = RELEASE_PATCH.read_bytes()
    regression = RELEASE_TEST.read_bytes()
    if (hashlib.sha256(patch).hexdigest(), hashlib.sha256(regression).hexdigest()) != (RELEASE_PATCH_SHA, RELEASE_TEST_SHA):
        raise ValueError("native release-selection patch/regression differs")
    upgrade_patch = UPGRADE_PATCH.read_bytes()
    upgrade_regression = UPGRADE_TEST.read_bytes()
    if (hashlib.sha256(upgrade_patch).hexdigest(), hashlib.sha256(upgrade_regression).hexdigest()) != (UPGRADE_PATCH_SHA, UPGRADE_TEST_SHA):
        raise ValueError("upstream persistent-switch patch/regression differs")
    asset_patch = ASSET_PATCH.read_bytes()
    asset_regression = ASSET_TEST.read_bytes()
    if (hashlib.sha256(asset_patch).hexdigest(), hashlib.sha256(asset_regression).hexdigest()) != (ASSET_PATCH_SHA, ASSET_TEST_SHA):
        raise ValueError("upstream matrix asset repair/regression differs")
    ref.new_directory(destination)
    for name, sha in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(source, name), target)
        if ref.digest(target) != sha:
            raise ValueError("pinned source changed during candidate creation: " + name)
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], input=patch,
                   cwd=destination, check=True, timeout=30)
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], input=upgrade_patch,
                   cwd=destination, check=True, timeout=30)
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], input=asset_patch,
                   cwd=destination, check=True, timeout=30)
    # parity.rs is explicitly excluded from build.rs's rendering LAW. Tests
    # live outside src. Neither edit changes the generation, key or rendering
    # digest; the runtime check below nevertheless enforces the original pair.
    target = destination / "crates/aurora-speaker-tuning/tests/native_release_attestation.rs"
    with target.open("xb") as stream:
        stream.write(regression)
    target = destination / "packaging/upgrade/tests/test_unit_detection.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(upgrade_regression)
    target = destination / "packaging/upgrade/tests/test_buzzkill_asset.py"
    with target.open("xb") as stream:
        stream.write(asset_regression)
    return ref.inventory(destination)


def safe_path(root, name):
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or str(rel) != name:
        raise ValueError("unsafe runtime path")
    root = root.absolute()
    target = root / name
    if any(p.is_symlink() for p in (root, *root.parents, target, *target.parents)):
        raise ValueError("symlink in runtime path")
    return target


def admit_tuning(ident, dump, request, gate):
    if (ident.get("verified"), ident.get("kind"), ident.get("board"), ident.get("unit_bound"),
            ident.get("unit_id"), ident.get("macos_build"), ident.get("aid"), ident.get("speaker_id")) != (
            True, "Installer", "j716s", True, request["unit_id"], ref.BUILD, "AID41", 3):
        raise ValueError("runtime tuning is not the paired J716s AID41/vendor3 bundle")
    tuning = dump["tuning"]
    if (tuning.get("refusals") or tuning.get("lsid") is not None
            or any(d.get("severity") == "error" for d in dump.get("diagnostics", {}).get("items", []))
            or tuning["protection"].get("kind") != "spp3"
            or len(tuning["protection"].get("lanes", [])) != 6):
        raise ValueError("runtime protection graph is not refusal-free six-lane Spp3")
    if "spp3" not in gate["units_at_parity"] or "j716s" in gate.get("denied_boards", []) or gate.get("board_overrides", {}).get("j716s") == "deny":
        raise ValueError("upstream protection policy refuses J716s")
    volume = tuning.get("volume_range")
    if not isinstance(volume, dict) or volume.get("source") != "Config/AID41.plist":
        raise ValueError("same-unit native HAL volume range missing; cubic fallback is not admitted")
    lo, hi = volume.get("min_db"), volume.get("max_db")
    import math
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lo, hi)) or not lo < hi <= 0:
        raise ValueError("same-unit native HAL volume range invalid")


def prepare(args):
    request = ref.checked_request(args.request, args.manifest_sha256)
    ref.checked_result(request, args.manifest_sha256, args.reference, args.reference_sha256)
    parity = ref.checked_parity(request, args.parity, args.parity_sha256)
    files = source_identity(args.source, args.source_archive)
    ref.new_directory(args.output)
    candidate = args.output.resolve() / "source"
    candidate_files = patched_source(args.source, candidate, files)
    verified_parity = args.output / "verified-parity.json"
    ref.save(verified_parity, parity)
    build = args.output.resolve() / "build"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SGV2_", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_TARGET"))}
    env["CARGO_TARGET_DIR"] = str(build)
    subprocess.run(["cargo", "build", "--offline", "--locked", "--release", "-p", "aurora-speaker-tuning",
                    "-p", "sgv2-supervisor", "-p", "sgv2-lv2"], cwd=candidate, env=env, check=True, timeout=1800)
    compiler = build / "release/aurora-speaker-tuning"
    bundle = args.bundle.resolve()
    ident = json.loads(ref.run([compiler, "identify", "--json", bundle]))
    dump = json.loads(ref.run([compiler, "dump", "--json", bundle]))
    gate = json.loads((candidate / "packaging/session/gate.json").read_text())
    admit_tuning(ident, dump, request, gate)
    key = json.loads(ref.run([compiler, "voicing-check", "--key-only", bundle]))
    if (key.get("key"), key.get("law")) != (request["key"], request["law"]):
        raise ValueError("runtime compiler law/tuning key differs from native parity")
    # This invokes every upstream gap, invariant and release/key/law check;
    # a passing native comparison alone does not replace these checks.
    checked = subprocess.run([str(compiler), "voicing-check", "--json", "--attestations", str(verified_parity), str(bundle)],
                             capture_output=True, text=True, timeout=900)
    if not checked.stdout.strip():
        raise ValueError("upstream runtime voicing check gave no JSON: " + checked.stderr.strip())
    check = json.loads(checked.stdout)
    ref.save(args.output / "runtime-voicing-check.json", check)
    if checked.returncode or check.get("result") != "pass":
        failed = [step["name"] + ": " + step.get("detail", "") for step in check.get("steps", []) if step.get("result") == "refused"]
        raise ValueError("upstream runtime voicing check refused: " + "; ".join(failed or [checked.stderr.strip()]))
    ref.run([compiler, "compile", "--board", "j716s", "--unit-id", request["unit_id"], "--voicing-pre",
             bundle, "-o", args.output / "offline-params.bin"])
    payload = args.output / "rootfs"
    modes = {}

    def copy(src, name, mode):
        dst = payload / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        dst.chmod(mode)
        modes[name] = mode

    for name in ("aurora-speaker-tuning", "speakerguardd"):
        copy(build / "release" / name, "usr/bin/" + name, 0o755)
    for name in ("wiring.py", "gen.py", "speakerguardd-gate", "speakerguardd-provision", "speakerguardd-selfcheck", "speakerguardd-session"):
        copy(candidate / "packaging/session" / name, "usr/libexec/speakerguardd/" + name, 0o755 if name.startswith("speakerguardd-") else 0o644)
    copy(candidate / "packaging/session/gate.json", "usr/share/speakerguardd/gate.json", 0o644)
    copy(verified_parity, "usr/share/speakerguardd/parity.json", 0o644)
    copy(build / "release/libsgv2_lv2.so", "usr/lib/lv2/sgv2.lv2/libsgv2_lv2.so", 0o755)
    for name in ("manifest.ttl", "sgv2.ttl"):
        copy(candidate / "crates/sgv2-lv2/lv2/sgv2.lv2" / name, "usr/lib/lv2/sgv2.lv2/" + name, 0o644)
    for name in SYSTEM_UNITS + USER_UNITS:
        copy(candidate / "packaging/systemd" / name, "usr/lib/systemd/" + ("user/" if name in USER_UNITS else "system/") + name, 0o644)
    upgrade = candidate / "packaging/upgrade"
    copy(upgrade / "aurora-speaker-upgrade", "usr/bin/aurora-speaker-upgrade", 0o755)
    for path in sorted((upgrade / "aurora_speaker_upgrade").rglob("*.py")):
        copy(path, "usr/lib/aurora-speaker-upgrade/" + path.relative_to(upgrade).as_posix(), 0o644)
    copy(upgrade / "share/restore-images.json", "usr/share/aurora-speaker-upgrade/restore-images.json", 0o644)
    copy(bundle, BUNDLE, 0o600)
    conf = payload / "etc/speakerguardd/provision.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("SGV2_SEAT_USER=" + args.seat_user + "\nSGV2_BUNDLE=/" + BUNDLE + "\n")
    modes[conf.relative_to(payload).as_posix()] = 0o644
    # Services run the ordinary upstream qualification gate. The additional
    # condition prevents boot/restart from skipping verify's numerical gate.
    condition = payload / "usr/lib/systemd/system/speakerguardd.service.d/20-native-voicing.conf"
    condition.parent.mkdir(parents=True)
    condition.write_text("[Service]\nExecCondition=/usr/bin/aurora-speaker-tuning voicing-check --attestations /usr/share/speakerguardd/parity.json --bundle ${SGV2_TUNING}\nTimeoutStartSec=600\n")
    modes[condition.relative_to(payload).as_posix()] = 0o644
    doc = {"schema": 1, "recipe": "j716s-guarded-speaker-runtime-v1", "source_commit": ref.REV,
           "source_archive_sha256": ref.ARCHIVE_SHA, "transport_patch_sha256": ref.PATCH_SHA,
           "native_release_patch_sha256": RELEASE_PATCH_SHA, "native_release_test_sha256": RELEASE_TEST_SHA,
           "upgrade_unit_patch_sha256": UPGRADE_PATCH_SHA, "upgrade_unit_test_sha256": UPGRADE_TEST_SHA,
           "upgrade_asset_patch_sha256": ASSET_PATCH_SHA, "upgrade_asset_test_sha256": ASSET_TEST_SHA,
           "base_source_files": files, "source_files": candidate_files, "unit_id": request["unit_id"], "macos_build": ref.BUILD,
           "attestation_build": dump["tuning"]["macos_build"], "rendering_law_build": dump["tuning"]["law_build"],
           "law": request["law"], "key": request["key"], "request_sha256": args.manifest_sha256,
           "reference_sha256": args.reference_sha256, "parity_sha256": args.parity_sha256,
           "bundle_digest": ident["digest"], "voicing_check": check, "files": ref.inventory(payload), "modes": modes,
           "playback_authorized": False, "native_reference_scope": "full-volume voicing; not a controller or volume-sweep measurement"}
    ref.save(args.output / MANIFEST, doc)
    print("PREPARED (not activated): " + ref.digest(args.output / MANIFEST))


def checked_package(root, expected):
    if ref.digest(root / MANIFEST) != expected:
        raise ValueError("external runtime manifest hash differs")
    doc = json.loads((root / MANIFEST).read_text())
    if (doc.get("schema"), doc.get("recipe"), doc.get("source_commit"), doc.get("source_archive_sha256"), doc.get("transport_patch_sha256"), doc.get("playback_authorized")) != (
            1, "j716s-guarded-speaker-runtime-v1", ref.REV, ref.ARCHIVE_SHA, ref.PATCH_SHA, False):
        raise ValueError("runtime package provenance differs")
    if (doc.get("native_release_patch_sha256"), doc.get("native_release_test_sha256")) != (RELEASE_PATCH_SHA, RELEASE_TEST_SHA):
        raise ValueError("runtime native release-selection repair binding differs")
    if (doc.get("upgrade_unit_patch_sha256"), doc.get("upgrade_unit_test_sha256")) != (UPGRADE_PATCH_SHA, UPGRADE_TEST_SHA):
        raise ValueError("runtime persistent-switch repair binding differs")
    if (doc.get("upgrade_asset_patch_sha256"), doc.get("upgrade_asset_test_sha256")) != (ASSET_PATCH_SHA, ASSET_TEST_SHA):
        raise ValueError("runtime matrix asset repair binding differs")
    if not doc.get("files") or doc.get("files").keys() != doc.get("modes", {}).keys():
        raise ValueError("runtime inventory/modes missing")
    for name, sha in doc["files"].items():
        if ref.digest(safe_path(root / "rootfs", name)) != sha or doc["modes"][name] not in (0o600, 0o644, 0o755):
            raise ValueError("runtime payload changed")
    if ref.inventory(root / "rootfs") != doc["files"]:
        raise ValueError("runtime payload inventory differs")
    return doc


def require_root_and_stopped(root):
    if root == Path("/"):
        if os.geteuid() != 0:
            raise ValueError("live runtime installation needs root")
        for unit in SYSTEM_UNITS:
            state = subprocess.run(["systemctl", "show", unit, "--property=ActiveState", "--value"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
            if state not in ("", "inactive", "failed"):
                raise ValueError("stop speaker units before installing/rolling back: " + unit)


def ensure_parents(path):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir()
        parent.chmod(0o755)


def install(args):
    doc = checked_package(args.package, args.package_sha256)
    root = args.root.absolute()
    require_root_and_stopped(root)
    if root == Path("/"):
        if (Path("/proc/device-tree/compatible").read_bytes().split(b"\0")[0] != b"apple,j716s"
                or hashlib.sha256(b"aurora-speaker-unit-v1\0" + Path("/proc/device-tree/serial-number").read_bytes().rstrip(b"\0")).hexdigest() != doc["unit_id"]):
            raise ValueError("runtime package belongs to another board/unit")
    receipt = safe_path(root, "var/lib/speakerguardd/" + RECEIPT)
    targets = [safe_path(root, name) for name in doc["files"]]
    if receipt.exists() or any(p.exists() for p in targets):
        raise ValueError("refusing existing runtime files; use the distribution package manager for upgrades")
    written = []
    try:
        for name, target in zip(doc["files"], targets):
            ensure_parents(target)
            with target.open("xb") as out:
                written.append(target)
                with (args.package / "rootfs" / name).open("rb") as src:
                    shutil.copyfileobj(src, out)
            target.chmod(doc["modes"][name])
            if ref.digest(target) != doc["files"][name]:
                raise ValueError("runtime payload changed during installation")
        ensure_parents(receipt)
        ref.save(receipt, {"schema": 1, "package_sha256": args.package_sha256, "files": doc["files"]})
    except Exception:
        for target in reversed(written):
            target.unlink()
        raise
    print("INSTALLED, services not enabled/started; rollback receipt SHA-256 " + ref.digest(receipt))


def rollback(args):
    root = args.root.absolute()
    require_root_and_stopped(root)
    receipt = safe_path(root, "var/lib/speakerguardd/" + RECEIPT)
    if ref.digest(receipt) != args.receipt_sha256:
        raise ValueError("external install receipt hash differs")
    doc = json.loads(receipt.read_text())
    if doc.get("schema") != 1 or not doc.get("files"):
        raise ValueError("invalid runtime install receipt")
    targets = [safe_path(root, name) for name in doc["files"]]
    for name, target in zip(doc["files"], targets):
        if ref.digest(target) != doc["files"][name]:
            raise ValueError("installed file changed; refusing rollback: " + name)
    for target in reversed(targets):
        target.unlink()
    receipt.unlink()
    print("ROLLED BACK package files; no legacy daemon or raw speaker route started")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--source", type=Path, default=SOURCE)
    for name in ("source-archive", "request", "reference", "parity", "bundle", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("manifest-sha256", "reference-sha256", "parity-sha256", "seat-user"):
        p.add_argument("--" + name, required=True)
    p = sub.add_parser("install")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--package-sha256", required=True)
    p.add_argument("--root", type=Path, default=Path("/"))
    p = sub.add_parser("rollback")
    p.add_argument("--receipt-sha256", required=True)
    p.add_argument("--root", type=Path, default=Path("/"))
    args = ap.parse_args()
    os.umask(0o077)
    try:
        if args.command == "prepare":
            import re
            if not re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", args.seat_user):
                raise ValueError("unsafe seat user name")
        {"prepare": prepare, "install": install, "rollback": rollback}[args.command](args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
