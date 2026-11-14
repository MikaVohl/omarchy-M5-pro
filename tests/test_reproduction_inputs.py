"""Behavioral guards for reproducible source extraction and fresh build outputs."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "source.tar.gz"
        self.destination = self.root / "source"
        self.payload = b"public source header\n"
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name in ("tree", "tree/deep"):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                bundle.addfile(info)
            info = tarfile.TarInfo("tree/target.h")
            info.size = len(self.payload)
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(self.payload))
            info = tarfile.TarInfo("tree/deep/link.h")
            info.type = tarfile.SYMTYPE
            info.linkname = ".././target.h"
            bundle.addfile(info)
        self.sources = load("source_inputs_test", "scripts/prepare-sources.py")

    def test_data_filter_normalization_preserves_consumable_source_link(self):
        with patch.object(self.sources, "SOURCES", self.root):
            self.sources.archive_tree(self.archive, self.destination, True)
            self.sources.archive_tree(self.archive, self.destination, False)
        self.assertEqual((self.destination / "deep/link.h").read_bytes(), self.payload)

    def test_changed_link_refuses_an_otherwise_unchanged_source_tree(self):
        with patch.object(self.sources, "SOURCES", self.root):
            self.sources.archive_tree(self.archive, self.destination, True)
            link = self.destination / "deep/link.h"
            link.unlink()
            link.symlink_to("../missing.h")
            with self.assertRaises(ValueError):
                self.sources.archive_tree(self.archive, self.destination, False)


class FreshKernelArtifactTests(unittest.TestCase):
    def test_fresh_signing_bytes_are_bound_and_later_tampering_refuses(self):
        payload = load("persistent_inputs_test", "outputs/persistent-install/persistent_payload.py")
        profile = json.loads((ROOT / "profiles/j716s.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory)
            inventory = {}
            for name in ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map",
                         "Module.symvers", "kernel.release", "modules.tar.gz"):
                data = (profile["kernel_release"] + "\n").encode() if name == "kernel.release" else (name + ": fresh build signing bytes\n").encode()
                (kernel / name).write_bytes(data)
                inventory[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            (kernel / "build-manifest.json").write_text(json.dumps({
                "schema": 1, "commit": profile["sources"]["kernel"]["commit"],
                "kernel_release": profile["kernel_release"], "artifacts": inventory}))
            payload.kernel_build(kernel)
            (kernel / "modules.tar.gz").write_bytes(b"altered module archive")
            with self.assertRaises(ValueError):
                payload.kernel_build(kernel)
