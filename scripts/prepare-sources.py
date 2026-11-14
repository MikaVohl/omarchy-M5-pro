#!/usr/bin/env python3
"""Fetch pinned public sources, or check an existing workspace without writing it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "work/sources"


def run(*args):
    return subprocess.check_output(args, text=True,
                                   env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).strip()


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_tree(archive, destination, fetch):
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        prefix = members[0].name.split("/")[0]
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != prefix or ".." in parts or Path(member.name).is_absolute():
                raise ValueError("unsafe source archive member")
        if fetch and not destination.exists():
            with tempfile.TemporaryDirectory(dir=SOURCES) as temporary:
                bundle.extractall(temporary, filter="data")
                Path(temporary, prefix).rename(destination)
        expected = set()
        for member in members:
            relative = Path(member.name).relative_to(prefix)
            if str(relative) == ".":
                continue
            expected.add(relative.as_posix())
            path = destination / relative
            if member.isdir():
                valid = path.is_dir() and not path.is_symlink()
            elif member.issym():
                filtered = tarfile.data_filter(member, str(SOURCES))
                valid = path.is_symlink() and os.readlink(path) == filtered.linkname
            elif member.isfile():
                valid = path.is_file() and not path.is_symlink()
                if valid:
                    with bundle.extractfile(member) as stream:
                        valid = sha256(path) == hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                raise ValueError(f"unsupported archive member: {member.name}")
            if not valid:
                raise ValueError(f"source differs from verified archive: {path}")
        actual = {path.relative_to(destination).as_posix() for path in destination.rglob("*")}
        if actual != expected:
            raise ValueError(f"source tree has missing or extra entries: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only; no network or workspace changes")
    mode.add_argument("--fetch", action="store_true", help="download and verify exact pinned sources")
    args = parser.parse_args()
    profile = json.loads((ROOT / "profiles/j716s.json").read_text())
    if args.fetch:
        SOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("kernel", "bringup", "mesa"):
        source = profile["sources"][name]
        commit = source["commit"]
        if len(commit) != 40 or commit not in source["url"]:
            raise ValueError(f"{name}: source URL must pin the full commit")
        archive = SOURCES / f"{name}-{commit}.tar.gz"
        if args.fetch and not archive.exists():
            with tempfile.NamedTemporaryFile(dir=SOURCES, delete=False) as temporary:
                pending = Path(temporary.name)
                try:
                    with urllib.request.urlopen(source["url"]) as response:
                        while chunk := response.read(1024 * 1024):
                            temporary.write(chunk)
                    temporary.close()
                    if sha256(pending) != source["sha256"]:
                        raise ValueError(f"{name}: archive SHA-256 mismatch")
                    pending.rename(archive)
                finally:
                    pending.unlink(missing_ok=True)
        if sha256(archive) != source["sha256"]:
            raise ValueError(f"{name}: archive SHA-256 mismatch")
        archive_tree(archive, SOURCES / name, args.fetch)
        print(f"{name}: verified {commit}")
    repositories = {"aura": profile["sources"]["aura"],
                    "asahi-installer": {
                        "url": "https://github.com/AsahiLinux/asahi-installer",
                        "commit": "99dff2e968dafcabc2a940865b051e91ffcfafd3"}}
    for name, source in repositories.items():
        destination = SOURCES / name
        if args.fetch and not destination.exists():
            with tempfile.TemporaryDirectory(dir=SOURCES) as temporary:
                checkout = Path(temporary) / name
                run("git", "init", str(checkout))
                run("git", "-C", str(checkout), "remote", "add", "origin", source["url"])
                run("git", "-C", str(checkout), "fetch", "--depth=1", "origin", source["commit"])
                run("git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD")
                checkout.rename(destination)
        if run("git", "-C", str(destination), "rev-parse", "HEAD") != source["commit"]:
            raise ValueError(f"{name}: commit mismatch")
        if run("git", "-C", str(destination), "remote", "get-url", "origin") != source["url"]:
            raise ValueError(f"{name}: origin mismatch")
        if run("git", "-C", str(destination), "status", "--porcelain", "--untracked-files=all"):
            raise ValueError(f"{name}: source checkout is modified")
        # The installer firmware package does not use its artwork/m1n1 gitlinks.
        # Leave these uninitialized rather than fetching unrelated bootloader trees.
        if name != "asahi-installer" and (destination / ".gitmodules").exists():
            raise ValueError(f"{name}: unexpected submodules require explicit source pins")
        print(f"{name}: verified {source['commit']}")


if __name__ == "__main__":
    main()
