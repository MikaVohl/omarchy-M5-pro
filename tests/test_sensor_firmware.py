"""Synthetic parser/security tests only; never read live ADT, disk or sensor."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

PROJECT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location("sensor_firmware", PROJECT / "scripts/prepare-sensor-firmware.py")
sensor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sensor)
FACTORY, EXTRACTOR = sensor.pinned_tools(PROJECT / "work/sources/kernel")

# Reuse the pinned reader's deterministic APFS builder, not another parser or
# scanner. This is test data only, never a usable firmware/private material file.
FIXTURE = PROJECT / "work/sources/kernel/tools/aurora-sep/tests/test_touchid_setup.py"
FIXTURE_HASH = "414798e393563b92c8979375209bc78f6e7afaca787440a7c04c6d6dcfaeff6a"
if hashlib.sha256(FIXTURE.read_bytes()).hexdigest() != FIXTURE_HASH:
    raise RuntimeError("pinned synthetic APFS fixture hash mismatch")
FX_SPEC = importlib.util.spec_from_file_location("pinned_factory_fixture", FIXTURE)
fx = importlib.util.module_from_spec(FX_SPEC)
FX_SPEC.loader.exec_module(fx)

IDENTITY = (sensor.CHIP, 0x0011223344556677, b"TESTSERIAL1")
SENSOR = fx.SENSOR
CAL = fx.im4p(b"CALB" + bytes(300), b"FSC2")
ROOT = fx.im4p(b"opaque test payload" + bytes(100), b"MSRk")
TRUST = b"opaque synthetic trust bundle" + bytes(64)
BOOT = {"unit_sha256": sensor.unit_digest(IDENTITY), "local_policy_sha256": "a" * 64,
        "asahi,os-fw-version": "b" * 64, "asahi,system-fw-version": "c" * 64}


def certificate():
    algorithm = fx.der(0x30, fx.der(0x06, bytes.fromhex("2a864886f70d01010b")))
    tbs = fx.der(0x30, fx.integer(1))
    return fx.der(0x30, tbs + algorithm + fx.der(0x03, b"\x00" + b"s" * 64))


def manifest(objects, *, signature=b"s" * 256, certificates=None, duplicate=None):
    body = b"".join(fx.private(name, fx.der(0x31, b"".join(fx.private(key, value)
                      for key, value in props.items()))) for name, props in objects.items())
    if duplicate is not None:
        body += fx.private(duplicate, fx.der(0x31, b""))
    certs = certificate() if certificates is None else certificates
    return fx.der(0x30, fx.text(b"IM4M") + fx.integer(0)
                  + fx.der(0x31, fx.private(b"MANB", fx.der(0x31, body)))
                  + fx.octets(signature) + fx.der(0x30, certs))


def seal(*, identity=IDENTITY, board=sensor.BOARD, cal=CAL, root=ROOT, **options):
    chip, ecid, serial = identity
    return manifest({b"MANP": {b"SrNm": fx.octets(serial), b"BORD": fx.integer(board),
                               b"CHIP": fx.integer(chip), b"ECID": fx.integer(ecid)},
                     b"FSC2": {b"DGST": fx.octets(hashlib.sha384(cal).digest())},
                     b"MSRk": {b"DGST": fx.octets(hashlib.sha384(root).digest())}}, **options)


def records(*, identity=IDENTITY, board=sensor.BOARD, cal=CAL, root=ROOT):
    chip, ecid, _ = identity
    original = manifest({b"MANP": {b"srvn": fx.octets(b"\x01")}})
    return {f"FSC2-{SENSOR}": fx.img4(cal, original),
            f"MSRk-{SENSOR}": fx.img4(root, original),
            f"seal-{chip:08X}-{ecid:016X}": seal(identity=identity, board=board, cal=cal, root=root),
            "trustobject-current": TRUST,
            "trustobject-" + hashlib.sha256(TRUST).hexdigest().upper(): TRUST}


def image(files=None, **options):
    data = bytearray(fx.apfs(records() if files is None else files, **options))
    # Container UUID is mandatory provenance, absent in the upstream fixture.
    for block in (0, 1):
        at = block * fx.BS
        data[at + 72:at + 88] = bytes.fromhex("00112233445566778899aabbccddeeff")
        data[at:at + fx.BS] = fx.checksummed(bytes(data[at:at + fx.BS]))
    return bytes(data)


def report(evidence):
    return {"schema": 1, "recipe": sensor.RECIPE, "model": sensor.MODEL, "board": "j716s", "soc": "t6050",
            "profile_macos_build": sensor.BUILD, "kernel_commit": sensor.KERNEL_COMMIT,
            "tool_sha256": sensor.TOOL_HASHES, "boot": BOOT,
            "source": {"sha256": "d" * 64, "partition_name": "iBootSystemContainer",
                       "partition_type": sensor.ISC_TYPE, "partition_uuid": "00112233-4455-6677-8899-aabbccddeeff",
                       "size": 131072, "origin": "current-root-verified-raw-device"},
            "apfs": {"container_uuid": "00112233-4455-6677-8899-aabbccddeeff", "checkpoint_xid": 1},
            "files": evidence, "apple_signature_verification": "not-performed",
            "installation": "not-performed", "sensor_or_sep_actions": "none"}


class CurrentFirmwareIdentityTests(unittest.TestCase):
    def current(self, changes=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                root / "compatible": b"apple,j716s\x00apple,t6050\x00",
                root / "serial-number": IDENTITY[2] + b"\x00",
                root / "chosen/asahi,os-fw-version": b"27.3 beta1\x00",
                root / "chosen/asahi,system-fw-version": b"27.3 beta1\x00",
                root / "chosen/asahi,iboot1-version": b"mBoot-20457.60.65.0.1\x00",
                root / "chosen/asahi,iboot2-version": b"mBoot-20457.60.65.0.1\x00",
                root / "soc/sep/compatible": b"apple,sep\x00",
                root / "soc/sep/local-policy-manifest": seal(),
            }
            for name, data in (changes or {}).items():
                if data is None:
                    values.pop(root / name, None)
                else:
                    values[root / name] = data
            for path, data in values.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            def read_kernel_property(path, *args, **kwargs):
                if Path(path) not in values:
                    raise FileNotFoundError(path)
                return values[Path(path)]
            # Kernel-generated sysfs files are modelled as root-controlled here;
            # filesystem ownership/link checks have separate package regressions.
            with patch.object(sensor, "DT", root), patch.object(sensor.os, "geteuid", return_value=0), \
                    patch.object(sensor, "read_input", side_effect=read_kernel_property):
                return sensor.current_identity(FACTORY, EXTRACTOR)

    def test_live_label_and_exact_mboot_pair_admit_current_policy_identity(self):
        identity, evidence = self.current()
        self.assertEqual(identity, IDENTITY)
        self.assertEqual(evidence["unit_sha256"], sensor.unit_digest(IDENTITY))

    def test_matching_label_cannot_hide_another_system_or_os_mboot(self):
        for name in ("chosen/asahi,iboot1-version", "chosen/asahi,iboot2-version"):
            with self.subTest(name=name), self.assertRaises(sensor.Refused):
                self.current({name: b"mBoot-20457.60.65.0.2\x00"})

    def test_missing_forwarded_policy_refuses_without_following_raw_addresses(self):
        with self.assertRaisesRegex(sensor.Refused, "forwarded local-policy-manifest"):
            self.current({"soc/sep/local-policy-manifest": None})


class FactoryTests(unittest.TestCase):
    def assert_refused(self, call):
        with self.assertRaises((sensor.Refused, FACTORY["Refused"], EXTRACTOR["DERError"])):
            call()

    def test_same_unit_rebuilds_both_canonical_comb_containers(self):
        outputs, evidence = sensor.prepare_records(records(), IDENTITY, FACTORY, EXTRACTOR)
        self.assertEqual(set(outputs), set(sensor.NAMES))
        for name, kind in sensor.NAMES.items():
            nested, trust = FACTORY["combined"](outputs[name])
            payload, signed_seal = FACTORY["image"](nested, kind)
            self.assertEqual(payload, CAL if kind == b"FSC2" else ROOT)
            self.assertEqual(signed_seal, seal())
            self.assertEqual(trust, TRUST)
            self.assertEqual(evidence[name]["sha256"], sensor.digest(outputs[name]))
        serialized = json.dumps(evidence)
        self.assertNotIn(IDENTITY[2].decode(), serialized)
        self.assertNotIn(SENSOR, serialized)

    def test_other_board_chip_unit_serial_and_mixed_sensor_are_refused(self):
        for name, files in {
            "board": records(board=13),
            "chip": records(identity=(0x8132, IDENTITY[1], IDENTITY[2])),
            "ecid": records(identity=(sensor.CHIP, IDENTITY[1] + 1, IDENTITY[2])),
            "serial": records(identity=(sensor.CHIP, IDENTITY[1], b"OTHERSERIAL")),
        }.items():
            with self.subTest(name):
                self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))
        files = records()
        files["MSRk-00003356-1123456789ABCDEF0123456789ABCDEF"] = files.pop(f"MSRk-{SENSOR}")
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))

    def test_missing_root_seal_trust_and_duplicate_records_are_refused(self):
        original = records()
        for key in list(original):
            if key == "trustobject-current":
                continue  # A single hash-named trustobject is explicitly supported.
            with self.subTest(key):
                files = dict(original)
                del files[key]
                self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))
        files = records()
        files["FSC2-00003356-1123456789ABCDEF0123456789ABCDEF"] = files[f"FSC2-{SENSOR}"]
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))

    def test_payload_digest_tampering_and_missing_calb_are_refused(self):
        files = records()
        original = manifest({b"MANP": {}})
        files[f"FSC2-{SENSOR}"] = fx.img4(fx.im4p(b"CALB" + bytes(301)), original)
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))
        files = records(cal=fx.im4p(b"invalid calibration"))
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))
        files = records(root=fx.im4p(b"", b"MSRk"))
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))

    def test_signature_certificate_duplicates_trailing_and_wrong_type_are_refused(self):
        for name, bad in {
            "signature absent": seal(signature=b""),
            "signature too short": seal(signature=b"x"),
            "certificates absent": seal(certificates=b""),
            "certificates empty": seal(certificates=fx.der(0x30, b"")),
            "duplicate MANP": seal(duplicate=b"MANP"),
            "trailing": seal() + b"\x00",
        }.items():
            with self.subTest(name):
                self.assert_refused(lambda: sensor.strict_manifest(bad, FACTORY, EXTRACTOR))
        files = records(cal=fx.im4p(b"CALB" + bytes(300), b"FSCl"))
        self.assert_refused(lambda: sensor.prepare_records(files, IDENTITY, FACTORY, EXTRACTOR))

    def test_read_only_apfs_factory_provenance_and_unsupported_layouts(self):
        good = image()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.apfs"
            path.write_bytes(good)
            fd = os.open(path, os.O_RDONLY)
            try:
                found, provenance = sensor.read_factory(fd, len(good), FACTORY)
                self.assertEqual(found, records())
                self.assertEqual(provenance["checkpoint_xid"], 1)
                self.assertEqual(provenance["container_uuid"], "00112233-4455-6677-8899-aabbccddeeff")
                self.assert_refused(lambda: sensor.read_factory(fd, len(good) - fx.BS, FACTORY))
            finally:
                os.close(fd)
            damaged = bytearray(good)
            damaged[10 * fx.BS + 2000] ^= 1
            for bad in (bytes(damaged), image(flags=0), image(bsd_flags=0x20),
                        image(role=0x10, extra_volume=False), image(path=("FactoryData",))):
                path.write_bytes(bad)
                fd = os.open(path, os.O_RDONLY)
                try:
                    self.assert_refused(lambda: sensor.read_factory(fd, len(bad), FACTORY))
                finally:
                    os.close(fd)



    def test_integer_identity_metadata_is_canonical_and_present(self):
        for value in (None, (0x04, b"\x0c"), (0x02, b""), (0x02, b"\xff"), (0x02, b"\x00\x0c")):
            properties = {} if value is None else {b"BORD": value}
            with self.subTest(value), self.assertRaises(sensor.Refused):
                sensor.integer_property(properties, b"BORD", sensor.BOARD)


class PackageSecurityTests(unittest.TestCase):
    def test_image_from_another_source_is_rejected_without_writing_any_source(self):
        good = image()
        wrong = good[:-1] + bytes([good[-1] ^ 1])
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            device = parent / "synthetic-current-partition"
            source = parent / "synthetic-other-image"
            device.write_bytes(good)
            source.write_bytes(wrong)
            source.chmod(0o600)
            args = SimpleNamespace(device=device, partition_uuid="00112233-4455-6677-8899-aabbccddeeff",
                                   source=source, source_sha256=sensor.digest(wrong), output=parent / "out")
            fd = os.open(device, os.O_RDONLY)
            try:
                with patch.object(sensor, "current_identity", return_value=(IDENTITY, BOOT)), \
                        patch.object(sensor, "device_binding", return_value=(os.dup(fd), {"size": len(good)})):
                    with self.assertRaises(sensor.Refused):
                        sensor.prepare(args, FACTORY, EXTRACTOR)
                self.assertFalse(args.output.exists())
                self.assertEqual(device.read_bytes(), good)
                self.assertEqual(source.read_bytes(), wrong)
            finally:
                os.close(fd)


    def test_symlink_traversal_hardlink_and_public_input_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            data = parent / "data"
            data.write_bytes(b"private")
            data.chmod(0o600)
            link = parent / "link"
            link.symlink_to(data)
            ancestor = parent / "alias"
            ancestor.symlink_to(parent, target_is_directory=True)
            for path in (link, ancestor / "data", parent / ".." / parent.name / "data"):
                with self.subTest(path), self.assertRaises((sensor.Refused, OSError)):
                    sensor.read_input(path, 64, private=True)
            hardlink = parent / "hardlink"
            os.link(data, hardlink)
            with self.assertRaises(sensor.Refused):
                sensor.read_input(data, 64, private=True)
            hardlink.unlink()
            data.chmod(0o644)
            with self.assertRaises(sensor.Refused):
                sensor.read_input(data, 64, private=True)

    def test_fresh_outputs_external_anchor_and_lightweight_check(self):
        outputs, evidence = sensor.prepare_records(records(), IDENTITY, FACTORY, EXTRACTOR)
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "private-package"
            anchor = sensor.write_package(package, outputs, report(evidence))
            for path in (package, package / "apple"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            for path in (package / sensor.REPORT, *(package / name for name in sensor.NAMES)):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                sensor.write_package(package, outputs, report(evidence))
            with patch.object(sensor, "current_identity", return_value=(IDENTITY, BOOT)), \
                    patch.object(sensor, "device_binding", side_effect=AssertionError("runtime must not read ISC")):
                checked, anchored = sensor.checked_package(package, anchor, tools=(FACTORY, EXTRACTOR))
                self.assertEqual(checked, outputs)
                self.assertEqual(anchored["files"], evidence)
                with self.assertRaises(sensor.Refused):
                    sensor.checked_package(package, "0" * 64, tools=(FACTORY, EXTRACTOR))
                target = package / "apple/mesacal-j716s.bin"
                target.write_bytes(outputs["apple/mesacal-j716s.bin"] + b"\x00")
                with self.assertRaises((sensor.Refused, FACTORY["Refused"])):
                    sensor.checked_package(package, anchor, tools=(FACTORY, EXTRACTOR))

    def test_report_tampering_wrong_unit_and_unexpected_entries_are_refused(self):
        outputs, evidence = sensor.prepare_records(records(), IDENTITY, FACTORY, EXTRACTOR)
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "package"
            anchor = sensor.write_package(package, outputs, report(evidence))
            with patch.object(sensor, "current_identity", return_value=(IDENTITY, dict(BOOT, unit_sha256="f" * 64))):
                with self.assertRaises(sensor.Refused):
                    sensor.checked_package(package, anchor, tools=(FACTORY, EXTRACTOR))
            path = package / sensor.REPORT
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaises(sensor.Refused):
                sensor.checked_package(package, anchor, tools=(FACTORY, EXTRACTOR))
            (package / "extra").write_text("not firmware")
            with self.assertRaises(sensor.Refused):
                sensor.private_package(package)

    def test_pinned_reader_tampering_is_refused_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            kernel = Path(temporary)
            directory = kernel / "tools/aurora-sep"
            directory.mkdir(parents=True)
            for name in sensor.TOOL_HASHES:
                source = PROJECT / "work/sources/kernel/tools/aurora-sep" / name
                (directory / name).write_bytes(source.read_bytes())
            target = directory / "aurora-touchid-setup"
            target.write_bytes(target.read_bytes() + b"\nraise RuntimeError('executed altered source')\n")
            with self.assertRaises(sensor.Refused):
                sensor.pinned_tools(kernel)

    def test_duplicate_json_and_failure_logs_do_not_expose_material(self):
        with self.assertRaises(sensor.Refused):
            json.loads('{"schema":1,"schema":1}', object_pairs_hook=sensor.no_duplicate_keys)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sensor, "pinned_tools", side_effect=RuntimeError("SECRET RAW CALIBRATION CERTIFICATE")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = sensor.main(["check", "--package", "/unused", "--provenance-sha256", "0" * 64])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("SECRET", stderr.getvalue())
        self.assertNotIn("CERTIFICATE", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
