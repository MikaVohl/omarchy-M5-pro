#!/usr/bin/env python3
"""Regenerate publication-only deployment derivative hashes; never access target hardware."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = (
    "finish-install-j716s.sh", "finish-install-j716s.py", "prepare-stage1-j716s.sh",
    "finish-install-step2-j716s.sh", "mount-targets-j716s.sh", "prep-stub-j716s.py",
    "prep-stub-j716s.sh", "preflight.py", "partition_metadata.py", "update-deployment-provenance.py",
)
REFERENCES = {
    "reference-source/finish-install.sh": "f548b0af4862927635b4d93694cb52c336219e4afad31d51fe6fb5775a733105",
    "reference-source/prep-stub.sh": "ef36f4f4db73899b86bb4c7e6e13da6300b58f4c51da82fcbbff9faa37cb7253",
}
TOOLS = ("fill_stage1_config.py", "enroll-stage1.sh")


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def generate():
    for name, expected in REFERENCES.items():
        if sha256(HERE / name) != expected:
            raise ValueError("Pinned reference source changed: " + name)
    return {
        "schema": 1,
        "source_project": "https://github.com/betizzel/omarchy-m5",
        "source_commit": "7974606ab1de0d41737f2c8784a7e86987612ce1",
        "reference_sha256": REFERENCES,
        "files_sha256": {name: sha256(HERE / name) for name in FILES},
        "tools_sha256": {name: sha256(HERE.parent / "boot-candidate/enrollment-tools" / name) for name in TOOLS},
        "upstream_tool_sha256": {
            "fill_stage1_config.py": "773ffcb0da679c1e5d96f870a43373d59707ab86bbeb33582fb9a1c662d0bbbf",
            "enroll-stage1.sh": "2b8e92477975828320a7db043cdd9a7a724fca8cd47d494b2c2e4852cb148a61",
        },
        "changes": [
            "Discover and journal fresh running-root source identities and exact Apple partition baseline",
            "Require fresh physical backup confirmation, explicit apply and paired-volume owner authentication",
            "Consume fresh pinned kernel, qualified-patch loader and ESP-specific rescue manifests",
            "Raw Stage 1 only; remove failed-Mach-O replacement and dated host-instance paths",
            "Bind Recovery resources, copied assets, source group and complete final partition layout",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="default: print newly calculated provenance only")
    modes.add_argument("--apply", action="store_true", help="write derivative JSON in this publication copy only")
    args = parser.parse_args()
    encoded = json.dumps(generate(), indent=2) + "\n"
    if args.apply:
        (HERE / "finish-install-derivative.json").write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
