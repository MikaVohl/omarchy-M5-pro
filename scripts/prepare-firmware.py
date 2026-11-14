#!/usr/bin/env python3
"""Prepare pinned J716s Linux firmware from a complete, locally supplied IPSW.

Does not download Apple firmware or contact hardware. Outputs contain firmware
and are private build artifacts, not files to publish or commit.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipsw", type=Path, required=True, help="complete local IPSW matching profiles/j716s.json")
    parser.add_argument("--installer", type=Path, help="immutable Asahi installer v0.9.1 source; otherwise exact Fedora RPM")
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/firmware/j716s-26C5035r-linux")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Refusing existing firmware output directory")
    trackpad = load("j716s_trackpad", PROJECT / "work/j716s-prep/extract-tpmtfw-reference.py")
    n1 = load("j716s_n1", PROJECT / "reference-source/mk-ftab-w3.py")
    expected = trackpad.profile()
    trackpad.load_converter(args.installer)
    with trackpad.verified_ipsw(args.ipsw, expected) as source:
        selected, build_manifest_sha256 = trackpad.identity(source, expected)
        ftab_path = "Firmware/t2026macG1/Release/ftab.bin"
        manifest_path = expected["n1"]["manifest_path"]
        ftab, manifest = source.read(ftab_path), source.read(manifest_path)
        if trackpad.sha(ftab) != expected["n1"]["input_sha256"]:
            raise ValueError("IPSW N1 FTAB differs from profile")
        if trackpad.sha(manifest) != expected["n1"]["manifest_sha256"]:
            raise ValueError("IPSW N1 global IM4M differs from profile")
        with tempfile.TemporaryDirectory(prefix="j716s-n1-") as temporary:
            firmware_input, manifest_input = Path(temporary) / "ftab.bin", Path(temporary) / "global.im4m"
            firmware_input.write_bytes(ftab)
            manifest_input.write_bytes(manifest)
            n1_report = n1.build(firmware_input, manifest_input,
                                 args.output / "apple/centauri/j716s-ftab.bin",
                                 args.output / "n1-build-report.json")
        tp_report = trackpad.extract(source, selected, args.output, args.installer)
    provenance = {"schema": 1, "target_model": expected["model"], "board": expected["board"],
                  "macos_build": expected["macos_build"], "ipsw": expected["ipsw"],
                  "build_manifest_sha256": build_manifest_sha256,
                  "identity": {"device_class": "j716sap", "restore_behavior": "Erase", "variant": "macOS Customer"},
                  "firmware": {"apple/centauri/j716s-ftab.bin": n1_report["output_sha256"],
                               "apple/tpmtfw-j716s.bin": tp_report["output_sha256"]},
                  "n1_input_member": ftab_path, "n1_manifest_member": manifest_path,
                  "apple_certificate_chain_independently_verified": False}
    tp_report.update(schema=1, target_model=expected["model"], board=expected["board"],
                     macos_build=expected["macos_build"], ipsw=expected["ipsw"],
                     build_manifest_sha256=build_manifest_sha256)
    for filename, report in (("trackpad-build-report.json", tp_report), ("firmware-manifest.json", provenance)):
        with (args.output / filename).open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
