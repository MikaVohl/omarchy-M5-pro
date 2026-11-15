#!/usr/bin/env python3
"""Offline parser/security edge tests; no build, firmware execution or hardware.

Synthetic CM3 vectors and ProRes rows are parser fixtures, never installable firmware.
"""
import importlib.util
import json
import os
from pathlib import Path
import struct
import plistlib
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("media_firmware_edges", PROJECT / "scripts/prepare-avd-firmware.py")
avd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avd)
spec = importlib.util.spec_from_file_location("prores_firmware_edges", PROJECT / "scripts/prepare-prores-firmware.py")
prores = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prores)
spec = importlib.util.spec_from_file_location("media_macos_capture", PROJECT / "scripts/capture-media-macos.py")
media_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media_capture)
spec = importlib.util.spec_from_file_location("media_native_contract", PROJECT / "scripts/inspect-media-native.py")
media_native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media_native)


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


class ProResContainerTests(unittest.TestCase):
    @staticmethod
    def blocks():
        # Synthetic register rows are parser fixtures, never prepared candidates.
        return [[(i * 4, 0xff, i & 0xff) for i in range(n)] for n in prores.COUNTS]

    def test_exact_t6050_shape_and_control_block(self):
        data = prores.encode_apri(self.blocks())
        self.assertEqual(len(data), 2880)
        self.assertEqual(prores.validate_apri(data)["rows"], [82, 80, 62, 12])

    def test_header_shape_truncation_and_padding_refuse(self):
        valid = prores.encode_apri(self.blocks())
        variants = [b"", valid[:-1], valid + b"\0"]
        for offset, value in ((0, 0), (4, 2), (8, 0x6041), (12, 3), (16, 4), (20, 81)):
            changed = bytearray(valid)
            struct.pack_into("<I", changed, offset, value)
            variants.append(changed)
        for data in variants:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                prores.validate_apri(data)

    def test_bounds_duplicates_masks_values_and_types_refuse(self):
        for block in range(4):
            variants = [(1, 0xff, 0), (prores.LIMITS[block], 0xff, 0),
                        (4, 0xff, 0), (0, 0, 0), (0, 0xff, 0x100),
                        (-4, 0xff, 0), (0, 0x100000000, 0), (False, 0xff, 0)]
            for row in variants:
                rows = self.blocks()
                rows[block][0] = row
                with self.subTest(block=block, row=row), self.assertRaises(ValueError):
                    prores.encode_apri(rows)
        for rows in (self.blocks()[:3], self.blocks() + [[]]):
            with self.assertRaises(ValueError):
                prores.encode_apri(rows)

    def test_valid_but_synthetic_apri_is_not_the_pinned_firmware(self):
        data = prores.encode_apri(self.blocks())
        self.assertNotEqual(prores.sha(data), prores.FIRMWARE_SHA256)


class ProResNativeTableTests(unittest.TestCase):
    @staticmethod
    def fixture():
        data = bytearray(128)
        # Native list -> width-4 descriptor -> static mask/value payload.
        struct.pack_into("<QQ", data, 0, 0x0010000000000020, 0)
        struct.pack_into("<IIQ", data, 32, 0x40000000, 0, 0x0020000000000040)
        struct.pack_into("<IIQ", data, 64, 0xff, 7, 0)
        return data

    def test_static_native_row_and_ssw_translation(self):
        data = self.fixture()
        self.assertEqual(prores.native_rows(data, 0, 1), [(0, 0xff, 7)])
        self.assertEqual(prores.native_rows(data, 0, 1, 0x1000), [(0x1000, 0xff, 7)])

    def test_dynamic_width_reserved_pointer_and_terminator_refuse(self):
        mutations = ((0, "Q", 0), (0, "Q", 0x8010000000000020),
                     (0, "Q", 0x001000003fffffff), (8, "Q", 1),
                     (32, "I", 0x80000000), (36, "I", 1),
                     (40, "Q", 0), (72, "Q", 1))
        for offset, kind, value in mutations:
            data = self.fixture()
            struct.pack_into("<" + kind, data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                prores.native_rows(data, 0, 1)
        with self.assertRaises(ValueError):
            prores.native_rows(self.fixture()[:12], 0, 1)

    def test_unpinned_images_refuse_before_decoder(self):
        with self.assertRaises(ValueError):
            prores.decode_source(b"not an IM4P")
        with self.assertRaises(ValueError):
            prores.prepare_image(self.fixture())


class ProResPrivateInputTests(unittest.TestCase):
    def test_private_regular_input_and_security_refusals(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "private"
            source.write_bytes(b"private fixture")
            source.chmod(0o600)
            self.assertEqual(prores.private_input(source, 32), b"private fixture")
            with self.assertRaises(ValueError):
                prores.private_input(source, 4)
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                prores.private_input(link, 32)
            directory_link = root / "parent-link"
            directory_link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                prores.private_input(directory_link / "private", 32)
            source.chmod(0o644)
            with self.assertRaises(ValueError):
                prores.private_input(source, 32)
            source.chmod(0o600)
            other = root / "hardlink"
            os.link(source, other)
            with self.assertRaises(ValueError):
                prores.private_input(source, 32)

    def test_external_candidate_output_refuses_without_creation(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "candidate"
            with self.assertRaises(ValueError):
                prores.save_candidate(output, b"not a prepared image")
            self.assertFalse(output.exists())

    def test_duplicate_and_traversing_paths_refuse(self):
        with self.assertRaises(ValueError):
            prores.open_parent("outputs/firmware/../candidate")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                prores.write_new(parent, "receipt", b"first")
                with self.assertRaises(FileExistsError):
                    prores.write_new(parent, "receipt", b"second")
                self.assertEqual((root / "receipt").read_bytes(), b"first")
            finally:
                os.close(parent)


class MediaAcquisitionTests(unittest.TestCase):
    @staticmethod
    def fixture(root):
        # Synthetic registry metadata is a checker fixture, not a target capture.
        serial = "MEDIA-CHECKER-FIXTURE"
        unit = prores.sha(serial.encode("ascii"))
        files = {"platform.plist": plistlib.dumps([{"model": b"Mac17,8\0", "IOPlatformSerialNumber": serial}]),
                 "SystemVersion.plist": plistlib.dumps({"ProductBuildVersion": "26C5035r"}),
                 "kext-AppleProResHW.kext.plist": plistlib.dumps({"CFBundleVersion": "627"})}
        observations = []
        for name, selector in media_capture.QUERIES:
            files[name] = plistlib.dumps([])
            observations.append({"file": name, "command": ["/usr/sbin/ioreg", "-r", "-a", "-l", *selector],
                                 "roots": 0, "available": False})
        report = {"schema": 1, "kind": "paired-macos-read-only-media-acquisition",
                  "model": "Mac17,8", "board": "j716s", "macos_build": "26C5035r",
                  "unit_serial_sha256": unit, "ioreg_platform_sha256": prores.sha(files["platform.plist"]),
                  "kernel_commit": prores.KERNEL_COMMIT, "observations": observations,
                  "missing_contract": media_capture.MISSING, **media_capture.UNQUALIFIED,
                  "files": {name: {"bytes": len(data), "sha256": prores.sha(data)} for name, data in files.items()}}
        raw = media_capture.encoded(report)
        media_capture.save_new(root, {**files, media_capture.REPORT: raw})
        return unit, report, prores.sha(raw)

    def test_valid_checker_fixture_never_authorizes_dma(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            unit, report, anchor = self.fixture(root)
            result = media_capture.check(root, anchor, unit)
            self.assertTrue(result["checked"])
            self.assertFalse(result["first_dma_authorized"])
            self.assertTrue(all(not value for value in media_capture.UNQUALIFIED.values()))
            self.assertEqual(len(report["observations"]), len(media_capture.QUERIES))

    def test_external_anchor_and_unit_mismatch_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            unit, _, anchor = self.fixture(root)
            for changed_anchor, changed_unit in (("0" * 64, unit), (anchor, "0" * 64)):
                with self.subTest(anchor=changed_anchor), self.assertRaises(ValueError):
                    media_capture.check(root, changed_anchor, changed_unit)

    def test_forged_authorization_paths_availability_and_source_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            unit, report, _ = self.fixture(root)
            for kind in ("authorize", "traversal", "availability", "integrity"):
                changed = json.loads(media_capture.encoded(report))
                if kind == "authorize":
                    changed["first_dma_authorized"] = True
                elif kind == "traversal":
                    changed["files"]["../foreign.plist"] = {"bytes": 1, "sha256": "0" * 64}
                elif kind == "availability":
                    changed["observations"][0]["available"] = True
                else:
                    changed["files"]["SystemVersion.plist"]["sha256"] = "0" * 64
                raw = media_capture.encoded(changed)
                (root / media_capture.REPORT).write_bytes(raw)
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    media_capture.check(root, prores.sha(raw), unit)

    def test_registry_wrong_shapes_and_native_unpinned_source_refuse(self):
        self.assertEqual(media_capture.registry_roots(b""), 0)
        for value in ({}, [1], "not registry"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                media_capture.registry_roots(plistlib.dumps(value))
        with self.assertRaises(ValueError):
            media_native.source_facts(b"not the pinned decoded collection")
        for engine in media_native.FIRMWARE_PINS:
            with self.subTest(engine=engine), self.assertRaises(ValueError):
                media_native.firmware_facts(b"not the pinned IM4P", engine)
        self.assertEqual(media_native.AVE_LENGTHS[3:5], (0x6e08, 0x78c8))
        self.assertTrue(prores.FIRST_DMA_BLOCKERS)


if __name__ == "__main__":
    unittest.main()
