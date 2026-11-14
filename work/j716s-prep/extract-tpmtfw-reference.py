#!/usr/bin/env python3
"""Extract J716s trackpad firmware from the complete profile-pinned local IPSW.

Use Fedora's python3-asahi_firmware-0.9.1-1.fc43.aarch64 or the immutable
Asahi installer v0.9.1 source specified by --installer. No HTTP range shortcut:
the entire local IPSW's size and SHA-256 must match before any member is used.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import plistlib
import struct
import subprocess
import sys
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
INSTALLER_COMMIT = "99dff2e968dafcabc2a940865b051e91ffcfafd3"
CONVERTER_RPM = "python3-asahi_firmware-0.9.1-1.fc43.aarch64"
TRACKPAD_SHA256 = "0d982117cb2d2c1909454ccd331e5a88a6f72e61c0eff3e55c6d884e90a519b3"
# Git blob identities of the actual imported dependency closure at v0.9.1.
CONVERTER_BLOBS = {
    "__init__.py": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "multitouch.py": "bf3d3dc16d092dd5f02d93817a0d6260fb0a4632",
    "img4.py": "a713a74252d4825bf86b9f5599e392ae3b1e531c",
    "asn1.py": "5f703342d2583a4a44caac21b7801e08713ea0ac",
    "core.py": "3a5e5c9a267eb82b508543df270889a4b90f683c",
    "cpio.py": "80ac3b30c9c3618ca85d55f290b9e15cc2a08d6c",
}


def profile():
    value = json.loads((PROJECT / "profiles/j716s.json").read_text())
    if (value["model"], value["board"], value["macos_build"]) != ("Mac17,8", "j716s", "26C5035r"):
        raise ValueError("Not the qualified J716s 26C5035r profile")
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verified_ipsw(path, expected):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != expected["ipsw"]["size"]:
        raise ValueError("Local IPSW file/size differs from profile")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected["ipsw"]["sha256"]:
            raise ValueError("Local IPSW SHA-256 differs from profile")
    return zipfile.ZipFile(path)


def identity(source, expected):
    raw = source.read("BuildManifest.plist")
    manifest = plistlib.loads(raw)
    if manifest.get("ProductBuildVersion") != expected["macos_build"]:
        raise ValueError("IPSW BuildManifest has wrong macOS build")
    if expected["model"] not in manifest.get("SupportedProductTypes", []):
        raise ValueError("IPSW does not support Mac17,8")
    candidates = [item for item in manifest["BuildIdentities"]
                  if item["Info"].get("DeviceClass") == "j716sap"
                  and item["Info"].get("RestoreBehavior") == "Erase"
                  and item["Info"].get("Variant") == "macOS Customer"]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one j716sap / Erase / macOS Customer identity")
    return candidates[0], sha(raw)


def load_converter(installer):
    if not __debug__:
        raise ValueError("Run without -O: upstream converter uses assertions")
    if installer is not None:
        package = installer.resolve() / "asahi_firmware"
        for name, expected in CONVERTER_BLOBS.items():
            path = package / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("Missing pinned converter source: " + name)
            data = path.read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if digest != expected:
                raise ValueError("Converter source differs from v0.9.1: " + name)
        sys.path.insert(0, str(installer.resolve()))
        provenance = {"kind": "pinned-asahi-installer-source", "commit": INSTALLER_COMMIT,
                      "url": "https://github.com/AsahiLinux/asahi-installer", "git_blobs": CONVERTER_BLOBS}
    else:
        spec = importlib.util.find_spec("asahi_firmware")
        if spec is None or not spec.origin:
            raise ValueError("Install " + CONVERTER_RPM + " or pass --installer pinned v0.9.1 source")
        package = Path(spec.origin).parent
        rpm = subprocess.check_output(["rpm", "-qf", str(package / "multitouch.py")], text=True).strip()
        if rpm != CONVERTER_RPM:
            raise ValueError("Installed converter must be exactly " + CONVERTER_RPM)
        subprocess.run(["rpm", "-V", CONVERTER_RPM], check=True)
        provenance = {"kind": "fedora-rpm", "nevra": CONVERTER_RPM}
    # The immutable checkout must still pass prepare-sources.py --check afterward.
    sys.dont_write_bytecode = True
    from asahi_firmware.multitouch import MultitouchFWCollection
    if Path(sys.modules["asahi_firmware.multitouch"].__file__).resolve() != (package / "multitouch.py").resolve():
        raise ValueError("Converter imported from an unexpected path")
    return MultitouchFWCollection, provenance


def extract(source, selected, output, installer=None):
    converter, provenance = load_converter(installer)
    item = selected["Manifest"]["Multitouch"]["Info"]
    name = item["Path"]
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or not name.endswith(".im4p"):
        raise ValueError("Unsafe Multitouch member")
    if (not item.get("IsFUDFirmware") or item.get("IsLoadedByiBoot")
            or item.get("IsLoadedByiBootStage1")):
        raise ValueError("Multitouch is not Linux-loadable FUD firmware")
    data = source.read(name)
    destination = output / "apple/tpmtfw-j716s.bin"
    if destination.exists() or destination.is_symlink():
        raise ValueError("Refusing existing trackpad output")
    with tempfile.TemporaryDirectory(prefix="j716s-multitouch-") as temporary:
        fud = Path(temporary) / "fud_firmware"
        device = fud / "j716s"
        device.mkdir(parents=True)
        (device / "Multitouch.im4p").write_bytes(data)
        files = dict(converter(str(fud)).files())
        if "apple/tpmtfw-j716s.bin" not in files:
            raise ValueError("Converter produced no J716s trackpad firmware")
        result = files["apple/tpmtfw-j716s.bin"].data
    if len(result) < 32:
        raise ValueError("Truncated HIDF output")
    magic, version, header_size, length, interface = struct.unpack_from("<4sIIII", result)
    if (magic, version, header_size) != (b"HIDF", 1, 32) or length != len(result) - 32 or interface >= length:
        raise ValueError("Invalid HIDF output")
    if sha(result) != TRACKPAD_SHA256:
        raise ValueError("Trackpad firmware differs from qualified 26C5035r output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(result)
    return {"source_member": name, "source_sha256": sha(data), "output_sha256": sha(result),
            "output_bytes": len(result), "converter": provenance}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipsw", type=Path, required=True, help="complete profile-pinned local IPSW")
    parser.add_argument("--installer", type=Path, help="Asahi installer v0.9.1 immutable source checkout")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    expected = profile()
    with verified_ipsw(args.ipsw, expected) as source:
        selected, manifest_sha256 = identity(source, expected)
        report = extract(source, selected, args.out, args.installer)
    report.update(schema=1, target_model=expected["model"], board=expected["board"],
                  macos_build=expected["macos_build"], build_manifest_sha256=manifest_sha256,
                  ipsw=expected["ipsw"])
    receipt = args.out / "trackpad-build-report.json"
    with receipt.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
