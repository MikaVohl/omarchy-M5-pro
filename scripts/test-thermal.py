#!/usr/bin/env python3
"""Production decoder regressions; no hardware access or source-text assertions."""
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('thermal', Path(__file__).with_name('qualify-thermal.py'))
thermal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thermal)


def ce_record(words, name):
    return struct.pack('<7I16s', *words, name)


class NativeThermalMapping(unittest.TestCase):
    def setUp(self):
        # Hardware-description records observed in the exact live J716s ADT.
        self.ce = (ce_record((1, 0, 0, 2, 0, 0, 0), b'MACC0') +
                   ce_record((2, 0, 1, 4, 0, 0, 0), b'MACC1') +
                   ce_record((3, 0, 2, 1, 3, 0, 0), b'PACC_ANE'))
        self.ptd = struct.pack('<4I16s', 16, 0x258, 2, 12, b'FAST-DIE-CE')
        self.cpus = [(cpu, cpu // 6, b'apple,sawtooth' if cpu < 12 else b'apple,everest')
                     for cpu in range(18)]

    def test_exact_eighteen_core_membership(self):
        result = thermal.derive_mapping(self.cpus, self.ce, self.ptd)
        self.assertEqual([row['cpus'] for row in result],
                         [list(range(6)), list(range(6, 12)), list(range(12, 18))])
        self.assertEqual([row['lane'] for row in result], [0, 1, 2])

    def test_other_sku_is_not_admitted(self):
        with self.assertRaises(ValueError):
            thermal.derive_mapping(self.cpus[:15], self.ce, self.ptd)

    def test_lane_order_is_not_assumed(self):
        ce = self.ce[44:88] + self.ce[:44] + self.ce[88:]
        with self.assertRaises(ValueError):
            thermal.derive_mapping(self.cpus, ce, self.ptd)

    def test_cluster_identity_is_independent_of_lane_names(self):
        self.cpus[12] = (12, 2, b'apple,sawtooth')
        with self.assertRaises(ValueError):
            thermal.derive_mapping(self.cpus, self.ce, self.ptd)

    def test_duplicate_cpu_id_is_rejected(self):
        self.cpus[5] = self.cpus[4]
        with self.assertRaises(ValueError):
            thermal.derive_mapping(self.cpus, self.ce, self.ptd)

    def test_report_geometry_change_is_not_admitted(self):
        for ptd in (self.ptd[:-1], self.ptd + self.ptd,
                    struct.pack('<4I16s', 16, 0x259, 2, 12, b'FAST-DIE-CE')):
            with self.subTest(ptd=ptd), self.assertRaises(ValueError):
                thermal.derive_mapping(self.cpus, self.ce, ptd)


if __name__ == '__main__':
    unittest.main()
