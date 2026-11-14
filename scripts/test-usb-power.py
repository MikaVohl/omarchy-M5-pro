#!/usr/bin/env python3
"""Offline fixtures for the USB/power evidence and same-unit ALS tooling.

Runs no hardware commands or sleep; execute once at integration:
  python scripts/test-usb-power.py
"""
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


als = load("als_preparation", "prepare-als-calibration.py")
usb = load("usb_power_qualification", "qualify-usb-power.py")
PROFILE = {"model": "Mac17,8", "board": "j716s", "macos_build": "26C5035r"}
BLOB = b"\x00\xff\x13\x00factory calibration bytes\x00"


def put(root, name, data):
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def capture_fixture(root):
    root.mkdir()
    identity = plistlib.dumps([{"model": b"Mac17,8\0", "IOPlatformSerialNumber": "UNIT-A"}])
    source = plistlib.dumps([{"IORegistryEntryName": "als", "IORegistryEntryChildren": [
        {"IORegistryEntryChildren": [{"IORegistryEntryChildren": [{"CalibrationData": BLOB}]}]}]}])
    report = {"schema": 1, **PROFILE, "unit_serial_sha256": als.sha(b"UNIT-A"),
              "ioreg_als_sha256": als.sha(source), "ioreg_platform_sha256": als.sha(identity),
              "firmware": als.FIRMWARE, "firmware_sha256": als.sha(BLOB), "firmware_size": len(BLOB)}
    put(root, "platform.plist", identity)
    put(root, "als.plist", source)
    put(root, "capture.json", json.dumps(report))
    return root


def sysfs_fixture(root):
    dt = "/sys/firmware/devicetree/base"
    put(root, dt + "/compatible", b"apple,j716s\0apple,t6050\0apple,arm-platform\0")
    put(root, dt + "/serial-number", b"UNIT-A\0")
    put(root, "/proc/sys/kernel/random/boot_id", "fixture-boot\n")
    put(root, "/proc/cmdline", "pd_ignore_unused clk_ignore_unused idle=nop\n")
    put(root, "/proc/uptime", "100.00 1800.00\n")
    put(root, "/sys/power/state", "freeze mem\n")
    put(root, "/sys/power/mem_sleep", "[s2idle]\n")
    put(root, "/sys/power/pm_test", "[none] devices freezer\n")
    put(root, "/sys/devices/system/cpu/cpu0/cpuidle/state0/name", "WFI\n")
    put(root, "/sys/devices/system/cpu/cpu0/cpuidle/state0/usage", "123\n")
    for number, name in enumerate(("right", "left-front")):
        mapping = usb.PORTS[name]
        pd = root / f"sys/devices/platform/pd-{name}"
        port = pd / "typec" / f"port{number}"
        port.mkdir(parents=True)
        (port / "device").symlink_to(pd, target_is_directory=True)
        put(pd, "of_node/connector/label", mapping["label"] + "\0")
        put(port, "data_role", "host [device]\n")
        put(port, "power_role", "source [sink]\n")
        put(port, "power_operation_mode", "default\n")
        typec_class = root / "sys/class/typec"
        typec_class.mkdir(parents=True, exist_ok=True)
        (typec_class / port.name).symlink_to(port, target_is_directory=True)
        supply = pd / "power_supply" / f"pd-{name}"
        put(supply, "online", "0\n")
        power_class = root / "sys/class/power_supply"
        power_class.mkdir(parents=True, exist_ok=True)
        (power_class / supply.name).symlink_to(supply, target_is_directory=True)
        for key, driver in (("usb", "dwc3-apple"), ("phy", "apple-t6050-dp-phy"), ("acio", "thunderbolt-apple-acio")):
            if not mapping[key]:
                continue
            dev = root / "sys/devices/platform" / mapping[key]
            dev.mkdir()
            platform_class = root / "sys/bus/platform/devices"
            platform_class.mkdir(parents=True, exist_ok=True)
            (platform_class / mapping[key]).symlink_to(dev, target_is_directory=True)
            driver_dir = root / "sys/bus/platform/drivers" / driver
            driver_dir.mkdir(parents=True, exist_ok=True)
            (dev / "driver").symlink_to(driver_dir, target_is_directory=True)
    put(root, "/sys/class/power_supply/macsmc-battery/status", "Discharging\n")
    put(root, "/sys/class/power_supply/macsmc-battery/power_now", "-10000000\n")
    put(root, "/sys/class/power_supply/macsmc-battery/energy_now", "60000000\n")
    put(root, "/sys/class/hwmon/hwmon0/name", "macsmc_hwmon\n")
    put(root, "/sys/class/hwmon/hwmon0/temp79_label", "TTPD\n")
    put(root, "/sys/class/hwmon/hwmon0/temp79_input", "-306783232000\n")
    put(root, "/sys/bus/iio/devices/iio:device1/name", "aop-sensors-als\n")
    put(root, "/sys/bus/iio/devices/iio:device1/in_illuminance_input", "")
    return root


class CalibrationTests(unittest.TestCase):
    def test_exact_nested_blob_and_binary_plist(self):
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            data = plistlib.dumps([{"IORegistryEntryChildren": [{"CalibrationData": BLOB}]}], fmt=fmt)
            self.assertEqual(als.calibration(data), BLOB)

    def test_no_missing_empty_typed_or_ambiguous_calibration(self):
        for tree in ([], [{}], [{"CalibrationData": b""}], [{"CalibrationData": "not-data"}],
                     [{"CalibrationData": BLOB, "IORegistryEntryChildren": [{"CalibrationData": BLOB}]}],
                     [{"IORegistryEntryChildren": {}}], [42]):
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                als.calibration(plistlib.dumps(tree))

    def test_same_unit_and_pinned_profile_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = capture_fixture(root / "capture")
            serial = put(root, "serial", b"UNIT-A\0")
            report, blob = als.inspect(source, PROFILE, serial)
            self.assertEqual(blob, BLOB)
            self.assertTrue(report["target_unit_matches"])
            self.assertFalse(report["physically_qualified"])
            self.assertFalse(report["installed"])
            put(root, "serial", b"UNIT-B\0")
            with self.assertRaisesRegex(ValueError, "different physical unit"):
                als.inspect(source, PROFILE, serial)
            put(root, "serial", b"UNIT-A\0")
            with self.assertRaises(ValueError):
                als.inspect(source, {**PROFILE, "macos_build": "different"}, serial)
            put(source, "als.plist", plistlib.dumps([{"CalibrationData": b"other"}]))
            with self.assertRaises(ValueError):
                als.inspect(source, PROFILE, serial)

    def test_private_exclusive_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "firmware"
            als.save_new(output, {als.FIRMWARE: BLOB})
            self.assertEqual((output / als.FIRMWARE).read_bytes(), BLOB)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual((output / als.FIRMWARE).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                als.save_new(output, {als.FIRMWARE: b"overwrite"})
            self.assertEqual((output / als.FIRMWARE).read_bytes(), BLOB)

    def test_capture_refuses_wrong_platform_or_build(self):
        with patch.object(als.platform, "system", return_value="Linux"):
            with self.assertRaises(ValueError):
                als.capture(Path("unused"), PROFILE)
        with patch.object(als.platform, "system", return_value="Darwin"), \
             patch.object(als.subprocess, "check_output", side_effect=["Mac17,8\n", "different\n"]):
            with self.assertRaises(ValueError):
                als.capture(Path("unused"), PROFILE)


class EvidenceTests(unittest.TestCase):
    def test_unplugged_mapping_is_label_based_not_port_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = sysfs_fixture(Path(directory))
            with patch.object(usb.subprocess, "run") as run:
                report = usb.collect(root)
                run.assert_not_called()
            self.assertEqual(report["ports"]["left-front"]["typec"]["name"], "port1")
            self.assertEqual(report["ports"]["right"]["typec"]["name"], "port0")
            self.assertFalse(report["ports"]["left-front"]["typec"]["partner_present"])
            self.assertEqual(report["usb_devices"], {})
            self.assertEqual(report["thunderbolt_devices"], {})
            self.assertFalse(report["physically_qualified"])
            self.assertEqual(report["sensors"]["hwmon0"]["temperature_mC"]["TTPD"], "-306783232000")
            self.assertEqual(usb.observations(report, "left-front")["port_power_supplies"]["pd-left-front"]["online"], "0")

    def test_enodata_is_preserved_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = sysfs_fixture(Path(directory))
            original = Path.read_bytes

            def read_bytes(path):
                if path.name == "in_illuminance_input":
                    raise OSError(61, "No data available")
                return original(path)

            with patch.object(Path, "read_bytes", read_bytes):
                report = usb.collect(root)
            self.assertIsNone(report["iio"]["iio:device1"]["in_illuminance_input"])
            self.assertTrue(any(error.get("errno") == 61 for error in report["read_errors"]))

    def test_actual_usb_device_attribution_and_paired_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = sysfs_fixture(Path(directory))
            before = usb.collect(root)
            controller = root / "sys/devices/platform" / usb.PORTS["left-front"]["usb"]
            endpoint = put(controller, "xhci-hcd.0.auto/usb1/1-1/speed", "5000\n").parent
            bus = root / "sys/bus/usb/devices"
            bus.mkdir(parents=True)
            (bus / "1-1").symlink_to(endpoint, target_is_directory=True)
            after = usb.collect(root)
            self.assertEqual(after["usb_devices"]["1-1"]["port"], "left-front")
            self.assertEqual(usb.observations(after, "left-front")["usb_devices_on_controller"]["1-1"]["speed"], "5000")
            self.assertFalse(usb.compare(before, after)["physically_qualified"])
            self.assertTrue(usb.compare(before, after)["changes"])
            before["identity"]["boot_id"] = "wrong-boot"
            with self.assertRaises(ValueError):
                usb.compare(before, after)
            before["identity"]["boot_id"] = after["identity"]["boot_id"]
            before["identity"]["unit_serial_sha256"] = "wrong-unit"
            with self.assertRaises(ValueError):
                usb.compare(before, after)

    def test_wrong_board_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = sysfs_fixture(Path(directory))
            put(root, "/sys/firmware/devicetree/base/compatible", b"apple,j716c\0")
            with self.assertRaises(ValueError):
                usb.collect(root)

    def test_default_stdout_and_explicit_exclusive_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = sysfs_fixture(Path(directory))
            args = ["python", str(HERE / "qualify-usb-power.py"), "--root", str(root)]
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            report = json.loads(result.stdout)
            self.assertTrue(report["read_only"])
            output = root / "report.json"
            self.assertFalse(output.exists())
            subprocess.run([*args, "--output", str(output)], capture_output=True, text=True, check=True)
            content = output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            result = subprocess.run([*args, "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), content)
            result = subprocess.run([*args, "--case", "usb3", "--port", "left-front"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_campaign_never_executes_sleep(self):
        report = usb.plan()
        self.assertFalse(report["suspend_execution"])
        self.assertEqual(report["orientations"], [1, 2])
        self.assertEqual(report["ports"], ["left-front", "right"])
        self.assertIn("not TB qualification", report["cases"]["thunderbolt"])


if __name__ == "__main__":
    unittest.main()
