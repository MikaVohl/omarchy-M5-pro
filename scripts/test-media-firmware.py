#!/usr/bin/env python3
"""Offline parser/security edge tests; no build, firmware execution or hardware.

Synthetic CM3 vectors are only parser fixtures, never installable firmware.
"""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("media_firmware_edges", PROJECT / "scripts/prepare-avd-firmware.py")
avd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avd)


class ImageVectorTests(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(avd.IMAGE_SIZE)
        struct.pack_into("<II", self.data, 0, 0x10000000 + avd.IMAGE_SIZE, 0xc71)

    def test_exact_vectors_and_elf_start(self):
        self.assertEqual(avd.validate_image(self.data, 0xc70)["bytes"], avd.IMAGE_SIZE)
        with self.assertRaises(ValueError):
            avd.validate_image(self.data, 0xc74)

    def test_truncated_and_padded_images_refuse(self):
        for data in (b"", self.data[:7], self.data[:-1], self.data + b"\0"):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                avd.validate_image(data)

    def test_wrong_tier_stack_and_invalid_reset_refuse(self):
        cases = ((0x10010000, 0xc71), (0x10014000, 0xc71),
                 (0x10012000, 0), (0x10012000, 0xc70),
                 (0x10012000, 1), (0x10012000, avd.IMAGE_SIZE | 1))
        for sp, reset in cases:
            with self.subTest(sp=sp, reset=reset):
                struct.pack_into("<II", self.data, 0, sp, reset)
                with self.assertRaises(ValueError):
                    avd.validate_image(self.data)


class PackageInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_symlink_input_refuses(self):
        target = self.root / "target"
        target.write_bytes(b"not firmware")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            avd.regular_file(link)
        with self.assertRaises(ValueError):
            avd.checked_package(link)

    def test_wrong_board_or_recipe_refuses_before_image_is_opened(self):
        package = self.root / "candidate"
        package.mkdir()
        for identity in ({"schema": 1, "board": "j716c", "soc": "t6050"},
                         {"schema": 1, "target_model": "Mac17,8", "board": "j716s",
                          "soc": "t6050", "macos_build": "26A428", "recipe": avd.RECIPE},
                         {"schema": 1, "target_model": "Mac17,8", "board": "j716s",
                          "soc": "t6050", "macos_build": "26C5035r", "recipe": "other-tier"}):
            (package / "avd-build-report.json").write_text(json.dumps(identity))
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                avd.checked_package(package)

    def test_unpinned_archive_refuses_before_extraction(self):
        archive = self.root / "invalid.tar.gz"
        archive.write_bytes(b"not even an archive")
        destination = self.root / "uncreated"
        with self.assertRaises(ValueError):
            avd.extract_source(archive, destination)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
