#!/usr/bin/env python3
"""Require real current-boot compute evidence; do not query or alter the GPU."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def admit(receipt_path, icd_path, device):
    receipt_path = Path(receipt_path).resolve(strict=True)
    icd_path = Path(icd_path).resolve(strict=True)
    proof = json.loads(receipt_path.read_text())
    if proof.get("schema") != 1 or proof.get("status") != "hardware-compute-qualified":
        raise RuntimeError("Receipt is not a qualified hardware compute contract")
    if proof.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        raise RuntimeError("Compute receipt is not from the current boot")
    if proof.get("icd_manifest_sha256") != digest(icd_path):
        raise RuntimeError("Selected ICD manifest differs from the compute proof")
    library = json.loads(icd_path.read_text())["ICD"]["library_path"]
    if "/" not in library:
        raise RuntimeError("ICD must identify a concrete library path, not an ambiguous loader search name")
    library_path = (icd_path.parent / library).resolve(strict=True)
    if proof.get("icd_library_sha256") != digest(library_path):
        raise RuntimeError("Selected ICD library differs from the compute proof")
    name = proof.get("device_name", "")
    if proof.get("hardware") is not True or not name or re.search(r"llvmpipe|lavapipe|swiftshader|software", name, re.I):
        raise RuntimeError("Compute proof must identify the real hardware device")
    if not proof.get("device_uuid") or proof.get("llama_device") != device:
        raise RuntimeError("Selected llama device/UUID differs from the proof")
    if not re.fullmatch(r"Vulkan[0-9]+", device):
        raise RuntimeError("Select one explicit Vulkan device, not auto/CPU fallback")
    version = proof.get("api_version", [])
    if not isinstance(version, list) or len(version) != 3 or any(type(n) is not int for n in version) or tuple(version) < (1, 2, 0):
        raise RuntimeError("Observed Vulkan 1.2 or newer is required; version overrides are forbidden")
    if proof.get("compute_queue") is not True or proof.get("storageBuffer16BitAccess") is not True:
        raise RuntimeError("Mandatory compute/16-bit storage contract is absent; Vulkan candidate is blocked")
    if "VK_KHR_16bit_storage" not in proof.get("device_extensions", []):
        raise RuntimeError("Upstream-required VK_KHR_16bit_storage extension is absent")
    if proof.get("compute_dispatch_passed") is not True or proof.get("compute_readback_passed") is not True:
        raise RuntimeError("Actual shader dispatch and verified readback proof are required")
    if proof.get("validation_clean") is not True:
        raise RuntimeError("Compute validation is not clean")
    evidence = proof.get("evidence", [])
    if not evidence:
        raise RuntimeError("Receipt must retain hashed actual compute/validation evidence")
    for item in evidence:
        path = (receipt_path.parent / item["path"]).resolve(strict=True)
        if digest(path) != item["sha256"]:
            raise RuntimeError(f"Compute evidence digest differs: {path}")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("icd", type=Path)
    parser.add_argument("device", help="Explicit llama device, e.g. Vulkan0, observed during qualification")
    args = parser.parse_args()
    try:
        proof = admit(args.receipt, args.icd, args.device)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"Vulkan blocked: {error}", file=sys.stderr)
        return 1
    print(f"Current-boot compute proof admitted: {proof['device_name']} ({proof['device_uuid']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
