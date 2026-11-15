#!/usr/bin/env python3
"""Prepare, capture and compare private AID41 native macOS voicing references.

No audio device, speaker grant, firmware installation or daemon activation.
A passing voicing comparison is not Spp3 controller or hardware qualification.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import plistlib
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REV = "54c911c7a7098af91f90375ae1b8d09f8588c79a"
ARCHIVE_SHA = "92e70bdb6210393b386a50f0d3defa4243dd8ec0773002bc09caaaedbeba4964"
PATCH = ROOT / "patches/speakerguardd-j716s-transport.patch"
PATCH_SHA = "01afdcdd1cd9f7d1b63b9bc1a8aae4f07e8a494052d5231410022f7342c92d3f"
DSP_SHA = "dd86e4cd688e949c2d7cd1d07c0e0474808dd8529dd28f5d27cd817d25a5b221"
BUILD = "26C5035r"
REQUEST = "speaker-reference-request.json"
RESULT = "speaker-reference-result.json"
COMPILER_SHA = "ae2b718f35383dd9cb99f8c5ddee3bf98f6ab67aff0f152629ed6f69331c640f"


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular non-symlink file")
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(command, **kwargs):
    return subprocess.check_output([str(x) for x in command], text=True,
                                   timeout=900, **kwargs)


def save(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    path.chmod(0o600)


def new_directory(path):
    if path.is_symlink() or path.exists():
        raise ValueError("refusing an existing output")
    path.mkdir(mode=0o700)


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are not reference inputs")
        if path.is_file() and path not in (root / REQUEST, root / RESULT):
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def checked_document(root, name, expected):
    if root.is_symlink() or not root.is_dir() or digest(root / name) != expected:
        raise ValueError("external reference manifest hash differs")
    doc = json.loads((root / name).read_text())
    if doc.get("schema") != 1 or not isinstance(doc.get("files"), dict) or not doc["files"]:
        raise ValueError("invalid reference manifest")
    for name, sha in doc["files"].items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError("unsafe reference path")
        target = root / name
        if (any((root.joinpath(*rel.parts[:n])).is_symlink() for n in range(1, len(rel.parts) + 1))
                or not target.resolve().is_relative_to(root.resolve()) or digest(target) != sha):
            raise ValueError("reference input changed")
    if inventory(root) != doc["files"]:
        raise ValueError("reference inventory differs")
    return doc


def checked_request(root, expected):
    doc = checked_document(root, REQUEST, expected)
    if (doc.get("recipe"), doc.get("source_commit"), doc.get("source_archive_sha256"),
            doc.get("transport_patch_sha256"), doc.get("macos_build"), doc.get("audiodsp_sha256"),
            doc.get("aid"), doc.get("playback_authorized")) != (
            "j716s-native-voicing-reference-v1", REV, ARCHIVE_SHA, PATCH_SHA, BUILD, DSP_SHA, 41, False):
        raise ValueError("reference request provenance differs")
    unit = doc.get("unit_id")
    frames = doc.get("frames")
    if (doc.get("speaker_id") not in (0, 3) or not isinstance(unit, str) or len(unit) != 64
            or any(c not in "0123456789abcdef" for c in unit)
            or type(frames) is not int or not 0 < frames <= 48000 * 60):
        raise ValueError("reference unit/vendor/frame identity missing")
    return doc


def checked_result(request, request_sha, root, expected):
    doc = checked_document(root, RESULT, expected)
    if (doc.get("recipe"), doc.get("request_sha256"), doc.get("unit_id"),
            doc.get("macos_build"), doc.get("audiodsp_sha256"), doc.get("audio_device_opened")) != (
            "j716s-native-voicing-result-v1", request_sha, request["unit_id"], BUILD, DSP_SHA, False):
        raise ValueError("native reference identity/provenance differs")
    if doc.get("case") != "AID41-spid" + str(request["speaker_id"]):
        raise ValueError("native reference case differs from requested speaker vendor")
    case = root / "refs" / doc["case"]
    status = json.loads((case / "status.json").read_text())
    meta = json.loads((case / "meta.json").read_text())
    if status.get("rc") != 0 or meta.get("aid") != 41 or meta.get("channels") != 6:
        raise ValueError("native graph did not produce six AID41 lanes")
    if meta.get("frames") != request["frames"] or (case / "unit_in.f32").stat().st_size != request["frames"] * 6 * 4:
        raise ValueError("native reference frame count differs")
    with (case / "unit_in.f32").open("rb") as audio:
        while chunk := audio.read(65536):
            if not all(math.isfinite(value[0]) for value in struct.iter_unpack("<f", chunk)):
                raise ValueError("non-finite native reference audio")
    if any("modelled_box" in note for note in meta.get("param_wires", [])):
        raise ValueError("modelled AudioUnit is not a native reference")
    return doc


def prepare(args):
    if digest(args.source_archive) != ARCHIVE_SHA or digest(PATCH) != PATCH_SHA:
        raise ValueError("speaker source/transport patch differs")
    if digest(args.compiler) != args.compiler_sha256:
        raise ValueError("compiler differs from the supplied build binding")
    spec = importlib.util.spec_from_file_location("speaker_preparation", ROOT / "scripts/prepare-speaker-tuning.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with tempfile.TemporaryDirectory() as temp:
        collector = helper.collector(args.collector_archive, Path(temp))
        helper.verify(collector, args.package)
        meta = helper.checked_bundle(collector, args.package / helper.BUNDLE)
        with collector.opened_bundle(args.package / helper.BUNDLE) as (bundle, _):
            private_files = dict(bundle)
    new_directory(args.output)
    source = args.output / "source"
    source.mkdir(mode=0o700)
    prefix = "speakerguardd-" + REV
    with args.source_archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ARCHIVE_SHA:
            raise ValueError("speaker source archive changed")
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as tar:
            for member in tar:
                rel = PurePosixPath(member.name)
                if rel.is_absolute() or ".." in rel.parts or rel.parts[0] != prefix or not (member.isdir() or member.isfile()):
                    raise ValueError("unsafe speaker source member")
                member.name = str(PurePosixPath(*rel.parts[1:]))
                if member.name != ".":
                    tar.extract(member, source, filter="data")
    patch_bytes = PATCH.read_bytes()
    if hashlib.sha256(patch_bytes).hexdigest() != PATCH_SHA:
        raise ValueError("transport patch changed")
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1"], input=patch_bytes,
                   cwd=source, check=True, timeout=30)
    tuning = args.output / "tuning"
    tuning.mkdir(mode=0o700)
    for name, data in private_files.items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("unsafe tuning member")
        target = tuning / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o600)
    base = ["--speaker-id", str(meta["speaker_id"]), args.package / helper.BUNDLE]
    dump = json.loads(run([args.compiler, "dump", "--json", *base]))
    key = json.loads(run([args.compiler, "voicing-check", "--key-only", *base]))
    selection = dump["tuning"]["selection"]
    variant = {"aid": 41, "speaker_id": meta["speaker_id"], "also_speaker_ids": [],
               "tree": str(tuning.resolve()), "key": key["key"],
               **{k: Path(selection[k]).name if selection.get(k) else None for k in ("strip", "propstrip", "graph")}}
    save(args.output / "variants.json", {"law": key["law"], "build": BUILD, "variants": [variant]})
    run([sys.executable, source / "tools/diffrig/aidgate/gate_prog.py", args.output / "program"])
    frames = json.loads((args.output / "program.json").read_text())["frames"]
    doc = {"schema": 1, "recipe": "j716s-native-voicing-reference-v1", "source_commit": REV,
           "source_archive_sha256": ARCHIVE_SHA, "transport_patch_sha256": PATCH_SHA,
           "compiler_sha256": args.compiler_sha256, "macos_build": BUILD, "audiodsp_sha256": DSP_SHA,
           "unit_id": meta["unit_id"], "aid": 41, "speaker_id": meta["speaker_id"],
           "key": key["key"], "law": key["law"], "frames": frames, "playback_authorized": False,
           "files": inventory(args.output)}
    save(args.output / REQUEST, doc)
    print("PREPARED: private native voicing request; manifest SHA-256 " + digest(args.output / REQUEST))


def capture(args):
    request = checked_request(args.request, args.manifest_sha256)
    if sys.platform != "darwin":
        raise ValueError("native AudioUnit references require paired macOS")
    if run(["sw_vers", "-buildVersion"]).strip() != BUILD or run(["sysctl", "-n", "hw.model"]).strip() != "Mac17,8":
        raise ValueError("reference host is not the exact paired board/build")
    raw = subprocess.check_output(["ioreg", "-a", "-r", "-d", "1", "-c", "IOPlatformExpertDevice"], timeout=30)
    nodes = plistlib.loads(raw)
    serials = {n["IOPlatformSerialNumber"] for n in nodes if n.get("IOPlatformSerialNumber")}
    if len(serials) != 1 or hashlib.sha256(b"aurora-speaker-unit-v1\0" + next(iter(serials)).encode()).hexdigest() != request["unit_id"]:
        raise ValueError("reference host belongs to another unit")
    dsp = Path("/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP")
    if digest(dsp) != DSP_SHA:
        raise ValueError("native AudioDSP differs from retained exact-build image")
    new_directory(args.output)
    source = args.request / "source"
    host = args.output / "box_host"
    run(["clang", "-fobjc-arc", "-O2", "-framework", "AudioToolbox", "-framework", "Foundation",
         source / "tools/diffrig/macos/box_host.m", "-o", host])
    variant = json.loads((args.request / "variants.json").read_text())["variants"][0]
    case_name = "AID41-spid" + str(request["speaker_id"])
    case = args.output / "refs" / case_name
    case.mkdir(mode=0o700, parents=True)
    cmd = [sys.executable, source / "tools/diffrig/aidgate/gr.py", "--tunings", args.request / "tuning",
           "--aid", "41", "--graph", variant["graph"], "--strip", variant["strip"],
           "--in", args.request / "program.f32", "--out", case / "unit_in.f32", "--meta", case / "meta.json",
           "--box-host", host]
    if variant["propstrip"]:
        cmd += ["--propstrip", variant["propstrip"]]
    env = dict(os.environ)
    env.pop("AUDIODSP_PATH", None)
    with (case / "render.log").open("x") as log:
        result = subprocess.run([str(x) for x in cmd], stdout=log, stderr=subprocess.STDOUT, env=env, timeout=900)
    save(case / "status.json", {"rc": result.returncode})
    if result.returncode:
        raise ValueError("native reference refused; private render.log retained")
    doc = {"schema": 1, "recipe": "j716s-native-voicing-result-v1", "request_sha256": args.manifest_sha256,
           "unit_id": request["unit_id"], "macos_build": BUILD, "audiodsp_sha256": DSP_SHA,
           "case": case_name, "audio_device_opened": False, "files": inventory(args.output)}
    save(args.output / RESULT, doc)
    result_sha = digest(args.output / RESULT)
    checked_result(request, args.manifest_sha256, args.output, result_sha)
    print("CAPTURED: offline AudioUnit reference; result manifest SHA-256 " + result_sha)


def compare(args):
    request = checked_request(args.request, args.manifest_sha256)
    checked_result(request, args.manifest_sha256, args.reference, args.reference_sha256)
    if digest(args.bin / "aurora-speaker-tuning") != request["compiler_sha256"] or (args.bin / "REV").read_text().strip() != REV:
        raise ValueError("comparison compiler revision/build differs from request")
    variant_doc = json.loads((args.request / "variants.json").read_text())
    variant_doc["variants"][0]["tree"] = str((args.request / "tuning").resolve())
    new_directory(args.output)
    save(args.output / "variants.json", variant_doc)
    env = {**os.environ, "SGV2_MACOS_BUILD": BUILD}
    subprocess.run([sys.executable, str(args.request / "source/tools/diffrig/aidgate/attest.py"), "gate",
                    "--variants", str(args.output / "variants.json"), "--mac", str(args.reference / "refs"),
                    "--bin", str(args.bin), "--prog", str(args.request / "program"),
                    "--reference", "macOS 27.3 26C5035r native same-unit Audio Units; full-volume voicing only",
                    "--out", str(args.output / "parity.json"), "--details", str(args.output / "details"),
                    "--work", str(args.output / "work"), "--jobs", "1"], env=env, check=True, timeout=1800)
    entry = json.loads((args.output / "parity.json").read_text())["releases"][0]["entries"][0]
    print("VOICING " + entry["verdict"].upper() + ": " + entry["reason"])
    print("Full-volume comparison only: no controller, volume sweep, sense/interlock or playback authorization.")
    return 0 if entry["verdict"] == "pass" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    for name in ("source-archive", "collector-archive", "package", "compiler"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--compiler-sha256", default=COMPILER_SHA)
    for name in ("capture", "compare"):
        s = sub.add_parser(name)
        s.add_argument("--request", type=Path, required=True)
        s.add_argument("--manifest-sha256", required=True)
        if name == "compare":
            s.add_argument("--reference", type=Path, required=True)
            s.add_argument("--reference-sha256", required=True)
            s.add_argument("--bin", type=Path, required=True)
    for s in (p, *[sub.choices[n] for n in ("capture", "compare")]):
        s.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    try:
        return {"prepare": prepare, "capture": capture, "compare": compare}[args.command](args) or 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
