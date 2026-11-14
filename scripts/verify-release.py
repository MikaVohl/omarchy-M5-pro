#!/usr/bin/env python3
"""Verify published source hashes; does not verify generated machine/build receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def verify(root, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("invalid or empty release manifest")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe release path: " + name)
        target = root / relative
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError("missing, symlinked, or escaping source: " + name)
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("published source hash mismatch: " + name)
    return len(manifest["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count = verify(args.root, args.root / "release-manifest.json")
    except (OSError, ValueError, TypeError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 1
    print(f"Verified {count} published source files. Generated artifacts require their own manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
