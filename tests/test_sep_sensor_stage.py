"""Sensor-trial DT admission: exact pinned wiring, path-matched phandle renumbering only."""
import copy
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sep_stage", PROJECT / "scripts/stage-sep-probe.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def cells(*values):
    return struct.pack(">%dI" % len(values), *values)


SEP_POWER = "/soc@200000000/power-management@280900000/power-controller@298"
SPI_POWER = "/soc@200000000/power-management@280900000/power-controller@330"
NUB_POWER = "/soc@200000000/power-management@288300000/power-controller@88"
DART = "/soc/iommu@505060000"
CRYPTO = "/soc/crypto@50500c000"


def baseline():
    return {
        "/": {"compatible": b"apple,j716s\0apple,t6050\0"},
        "/aliases": {},
        "/clock-ref": {"phandle": cells(0x55)},
        "/soc@200000000/sep@502600000": {"status": b"disabled\0"},
        "/soc@200000000/mailbox@502608000": {"status": b"disabled\0"},
        SEP_POWER: {"label": b"sep\0", "status": b"disabled\0"},
        SPI_POWER: {"label": b"spi2\0", "status": b"disabled\0", "phandle": cells(0xbc)},
        NUB_POWER: {"label": b"nub-gpio\0", "status": b"disabled\0", "phandle": cells(0xd2)},
        "/soc/spi@505108000": {"status": b"disabled\0", "clocks": cells(0x9b, 0x1c3), "power-domains": cells(0xbc)},
        "/soc/pinctrl@2881e4000": {"status": b"disabled\0", "phandle": cells(0xd3), "power-domains": cells(0xd2),
                                   "gpio-ranges": cells(0xd3, 0, 0, 61), "interrupt-controller": b"",
                                   "#interrupt-cells": cells(2), "interrupt-parent": cells(1),
                                   "interrupts": cells(0, 0, 0x2c2, 4)},
        "/soc@200000000/pinctrl@29c000000": {"phandle": cells(0x7c), "gpio-reserved-ranges":
                                             cells(0, 80, 81, 25, 108, 26, 135, 4, 140, 81)},
        DART: {"phandle": cells(0xd0)},
        CRYPTO: {"iommus": cells(0xd0, 1), "power-domains": cells(0xd2)},
    }


def sensor_candidate(before):
    """The observed build: pinned wiring plus nub GPIO moved to 0xbd and 0xbd..0xd2 shifted by one."""
    after = copy.deepcopy(before)
    shift = {0xbc: 0xbc, 0xd0: 0xd1, 0xd2: 0xd3, 0xd3: 0xbd}
    after[SPI_POWER]["phandle"] = cells(shift[0xbc])
    after[NUB_POWER]["phandle"] = cells(shift[0xd2])
    after[DART]["phandle"] = cells(shift[0xd0])
    after[CRYPTO] = {"iommus": cells(shift[0xd0], 1), "power-domains": cells(shift[0xd2])}
    after["/aliases"]["sep"] = b"/soc@200000000/sep@502600000\0"
    after["/soc@200000000/mailbox@502608000"]["status"] = b"okay\0"
    after[SEP_POWER].update({"status": b"okay\0", "apple,always-on": b""})
    after[SPI_POWER]["status"] = after[NUB_POWER]["status"] = b"okay\0"
    after["/soc/spi@505108000"].update(status=b"okay\0", clocks=cells(0x55))
    after["/soc/pinctrl@2881e4000"] = {"status": b"okay\0", "phandle": cells(0xbd), "power-domains": cells(0xd3),
                                       "gpio-ranges": cells(0xbd, 0, 0, 61),
                                       "gpio-reserved-ranges": cells(0, 21, 22, 39)}
    after["/soc@200000000/pinctrl@29c000000"]["gpio-reserved-ranges"] = cells(0, 80, 81, 25, 108, 26, 135, 3, 140, 81)
    after["/soc/spi@505108000/fingerprint@0"] = {
        "compatible": b"apple,mesa-fingerprint\0", "reg": cells(0), "spi-max-frequency": cells(8000000),
        "spi-cpol": b"", "spi-cs-setup-delay-ns": cells(20), "spi-cs-hold-delay-ns": cells(20),
        "enable-gpios": cells(0xbd, 21, 0), "interrupt-parent": cells(0x7c), "interrupts": cells(138, 1),
        "firmware-name": b"apple/mesacal-j716s.bin\0apple/msrk-j716s.bin\0"}
    return after


class SensorDtDeltaTests(unittest.TestCase):
    def validate(self, before, after, variant="sep-sensor"):
        with tempfile.TemporaryDirectory() as temporary:
            esp = Path(temporary)
            (esp / "aurora/root").mkdir(parents=True)
            (esp / "aurora/root/t6050-j716s.dtb").write_bytes(b"baseline")
            stage.select(variant)
            try:
                with patch.object(stage, "ESP", esp), \
                        patch.object(stage.payload, "dtb_nodes", side_effect=[before, after]):
                    stage.validate_dtb_delta(b"candidate")
            finally:
                stage.select("sep-probe")

    def test_pinned_wiring_with_path_matched_renumbering_is_admitted(self):
        before = baseline()
        self.validate(before, sensor_candidate(before))

    def test_reference_left_pointing_at_old_phandle_is_refused(self):
        before = baseline()
        after = sensor_candidate(before)
        after[CRYPTO]["iommus"] = cells(0xd0, 1)  # stale number now names a different node
        with self.assertRaisesRegex(RuntimeError, "unsafe DT change: " + CRYPTO):
            self.validate(before, after)

    def test_any_additional_property_or_wiring_change_is_refused(self):
        before = baseline()
        for node, name, value in (("/soc/spi@505108000/fingerprint@0", "interrupts", cells(139, 1)),
                                  ("/soc/spi@505108000/fingerprint@0", "enable-gpios", cells(0xbd, 22, 0)),
                                  ("/soc@200000000/sep@502600000", "status", b"okay\0"),
                                  (DART, "status", b"disabled\0")):
            with self.subTest(node=node, name=name):
                after = sensor_candidate(before)
                after[node][name] = value
                with self.assertRaises(RuntimeError):
                    self.validate(before, after)

    def test_existing_nub_gpio_interrupt_consumer_blocks_controller_removal(self):
        before = baseline()
        before["/soc/lid"] = {"interrupt-parent": cells(0xd3)}
        after = sensor_candidate(before)
        after["/soc/lid"] = {"interrupt-parent": cells(0xbd)}
        with self.assertRaisesRegex(RuntimeError, "nub GPIO interrupt consumer"):
            self.validate(before, after)

    def test_discovery_variant_still_refuses_sensor_wiring(self):
        before = baseline()
        with self.assertRaises(RuntimeError):
            self.validate(before, sensor_candidate(before), "sep-probe")


if __name__ == "__main__":
    unittest.main()
