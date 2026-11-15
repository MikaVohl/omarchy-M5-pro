"""Synthetic systemd metadata regressions; never touches services or hardware."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurora_speaker_upgrade import upgrade as up


class V2UnitDetection(unittest.TestCase):
    def metadata(self, starts, conditions=None):
        conditions = conditions or {}

        def show(*args):
            self.assertEqual(args[:2], ("show", "-p"))
            self.assertEqual(args[3], "--value")
            unit = args[-1]
            values = starts if args[2] == "ExecStart" else conditions
            value = values.get(unit)
            return subprocess.CompletedProcess(args, 0 if value is not None else 1, value or "", "")

        return mock.patch.object(up, "systemctl", side_effect=show)

    def canonical_start(self):
        return "{ path=/usr/bin/speakerguardd ; argv[]=/usr/bin/speakerguardd $SGV2_ARGS ; ignore_errors=no ; }"

    def canonical_condition(self, command="v2", path="/usr/libexec/speakerguardd/speakerguardd-gate"):
        return "{ path=" + path + " ; argv[]=" + path + " " + command + " ; ignore_errors=no ; }"

    def test_canonical_upstream_unit_requires_actual_v2_gate_protocol(self):
        with self.metadata({"speakerguardd.service": self.canonical_start()},
                           {"speakerguardd.service": self.canonical_condition()}):
            self.assertEqual(up.v2_unit(), "speakerguardd.service")
            self.assertNotIn("speakerguardd.service", up.legacy_units())

    def test_legacy_canonical_name_and_parameter_words_alone_do_not_identify_v2(self):
        for start in (self.canonical_start(), "/usr/bin/speakerguardd --params blob --uid 1000 --pcm 1",
                      "/usr/bin/speakerguardd --config /etc/speakerguardd-v2.conf"):
            with self.subTest(start=start), self.metadata({"speakerguardd.service": start}):
                with self.assertRaisesRegex(up.Retry, "no identifiable in-graph"):
                    up.v2_unit()

    def test_wrong_gate_path_or_command_cannot_admit_canonical_legacy(self):
        for command, path in (("prepare", "/usr/libexec/speakerguardd/speakerguardd-gate"),
                              ("v2-other", "/usr/libexec/speakerguardd/speakerguardd-gate"),
                              ("v2", "/tmp/speakerguardd-gate")):
            with self.subTest(command=command, path=path), self.metadata(
                    {"speakerguardd.service": self.canonical_start()},
                    {"speakerguardd.service": self.canonical_condition(command, path)}):
                with self.assertRaises(up.Retry):
                    up.v2_unit()

    def test_absolute_v2_paths_in_legacy_arguments_are_not_executables(self):
        for argument in ("/usr/bin/speakerguardd-v2", "/usr/bin/speakerguardd"):
            start = "{ path=/usr/bin/legacy ; argv[]=/usr/bin/legacy " + argument + " ; }"
            with self.subTest(argument=argument), self.metadata(
                    {"speakerguardd.service": start},
                    {"speakerguardd.service": self.canonical_condition()}):
                with self.assertRaises(up.Retry):
                    up.v2_unit()

    def test_gate_path_and_v2_protocol_must_belong_to_the_same_command(self):
        condition = ("{ path=/usr/bin/legacy ; argv[]=/usr/bin/legacy /usr/libexec/speakerguardd/speakerguardd-gate v2 ; } "
                     + self.canonical_condition("prepare"))
        with self.metadata({"speakerguardd.service": self.canonical_start()},
                           {"speakerguardd.service": condition}):
            with self.assertRaises(up.Retry):
                up.v2_unit()

    def test_distribution_v2_binary_is_still_identified(self):
        with self.metadata({"speakerguardd-v2.service": "{ path=/usr/bin/speakerguardd-v2 ; argv[]=/usr/bin/speakerguardd-v2 $SGV2_ARGS ; }"}):
            self.assertEqual(up.v2_unit(), "speakerguardd-v2.service")
            self.assertIn("speakerguardd.service", up.legacy_units())

    def test_missing_or_misnamed_legacy_stack_never_uses_guessed_first_unit(self):
        for starts in ({}, {"speakerguardd-v2.service": self.canonical_start()}):
            with self.subTest(starts=starts), self.metadata(starts):
                with self.assertRaises(up.Retry):
                    up.v2_unit()


if __name__ == "__main__":
    unittest.main()
