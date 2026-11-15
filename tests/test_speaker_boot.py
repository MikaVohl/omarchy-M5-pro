"""Offline speaker boot-evidence parsing; no audio, services, or hardware calls."""
import importlib.util
import json
import re
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "speaker_boot", Path(__file__).resolve().parents[1] / "scripts/inspect-speaker-boot.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def journal_entry(microseconds, identifier, message, boot="full-kernel-boot"):
    return json.dumps({
        "__MONOTONIC_TIMESTAMP": str(microseconds),
        "_BOOT_ID": boot,
        "SYSLOG_IDENTIFIER": identifier,
        "MESSAGE": message,
    })


class SpeakerBootEvidence(unittest.TestCase):
    def test_daemon_grant_is_included_by_journal_filter(self):
        self.assertIsNotNone(re.search(m.JOURNAL_PATTERN, "protection granted"))
        record = m.parse_journal(journal_entry(10812230, "speakerguardd", "protection granted"))[0]
        self.assertEqual(m.event_kind(record), "supervisor_grant")

    def test_recorded_boot_order_does_not_attribute_lifetime_error(self):
        journal = "\n".join([
            journal_entry(4288011, "kernel", "snd-soc-macaudio sound: Speaker protection granted"),
            journal_entry(9683897, "systemd", "Finished Speaker protection graph for this user's PipeWire (speakerguardd v2)."),
            journal_entry(9819960, "speakerguardd-session", "speakerguardd-session: granted=0 links=1 carrier=1 report=unwritable"),
            journal_entry(10720456, "speakerguardd-session", "speakerguardd-session: granted=0 links=1 carrier=1 report=ok"),
            journal_entry(10816030, "kernel", "snd-soc-macaudio sound: Speaker protection granted"),
            journal_entry(10820200, "speakerguardd-session", "speakerguardd-session: granted=1 links=1 carrier=1 report=ok"),
            journal_entry(14060489, "pipewire", "spa.alsa: hw:CARD=AppleJ716,DEV=2c: follower delay:511 target:1024 thr:512 resample:0, resync (0 suppressed)"),
        ])
        records = m.parse_journal(journal)
        result = m.summarize(records, {"sgv2.speaker.j716": {"id": 34, "state": "R", "err": 1}}, 0)
        self.assertEqual(result["events"][1]["mono_ns"], 9683897000)
        self.assertEqual(result["events"][-1]["kind"], "sense_resync")
        self.assertEqual(result["kernel_grants"], [
            {"mono_ns": 4288011000, "refused_before_grant": 0},
            {"mono_ns": 10816030000, "refused_before_grant": 0},
        ])
        self.assertEqual(result["untimed_lifetime_errors"], {"sgv2.speaker.j716": 1})
        self.assertEqual(result["speaker_starts_refused"], 0)
        self.assertEqual(result["source_fix_evidence"], "insufficient")
        self.assertFalse(result["playback_authorized"])
        self.assertEqual(len(result["missing_causal_evidence"]), 3)

    def test_initial_pw_top_callback_is_not_a_zero_error_observation(self):
        text = "\n".join([
            "S ID QUANT RATE WAIT BUSY W/Q B/Q ERR FORMAT NAME",
            "C 34 0 0 --- --- --- --- 0 sgv2.speaker.j716",
            "C 35 0 0 --- --- --- --- 0 sgv2.sense.j716",
            "R 34 512 48000 727.0us 6.8us 0.07 0.00 1 S24_32 6 48000 sgv2.speaker.j716",
            "R 35 0 0 4.4us 12.0us 0.00 0.00 0 S16LE 12 48000 + sgv2.sense.j716",
        ])
        nodes = m.parse_pw_top(text)
        self.assertEqual(nodes["sgv2.speaker.j716"]["err"], 1)
        self.assertEqual(nodes["sgv2.sense.j716"]["err"], 0)
        self.assertEqual(m.parse_pw_top("C 34 0 0 --- --- --- --- 0 sgv2.speaker.j716"), {})

    def test_refused_starts_are_reported_without_subtracting_errors(self):
        records = m.parse_journal(journal_entry(
            10816030, "kernel",
            "snd-soc-macaudio sound: Speaker protection granted (2 speaker starts refused before)"
        ))
        result = m.summarize(records, {"sgv2.speaker.j716": {"id": 34, "state": "R", "err": 3}}, 2)
        self.assertEqual(result["kernel_grants"][0]["refused_before_grant"], 2)
        self.assertEqual(result["untimed_lifetime_errors"]["sgv2.speaker.j716"], 3)
        self.assertFalse(result["playback_authorized"])

    def test_missing_counter_is_unknown_not_zero(self):
        result = m.summarize([], {}, None)
        self.assertIsNone(result["speaker_starts_refused"])
        self.assertEqual(result["ungranted_start_evidence"], "kernel counter unavailable")
        self.assertFalse(result["playback_authorized"])

    def test_missing_monotonic_time_and_multiple_boots_are_refused(self):
        with self.assertRaisesRegex(ValueError, "monotonic timestamp"):
            m.parse_journal(json.dumps({"MESSAGE": "protection granted"}))
        records = m.parse_journal("\n".join([
            journal_entry(1, "kernel", "one", boot="a"),
            journal_entry(2, "kernel", "two", boot="b"),
        ]))
        with self.assertRaisesRegex(ValueError, "multiple boots"):
            m.summarize(records, {}, 0)

    def test_invalid_counter_and_last_real_stats_are_preserved(self):
        for invalid in (-1, True, "0"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "nonnegative integer"):
                m.summarize([], {}, invalid)
        nodes = m.parse_pw_top("\n".join([
            "R 34 512 48000 1us 1us 0.00 0.00 1 S24_32 6 48000 sgv2.speaker.j716",
            "R 34 512 48000 1us 1us 0.00 0.00 2 S24_32 6 48000 sgv2.speaker.j716",
        ]))
        self.assertEqual(nodes["sgv2.speaker.j716"]["err"], 2)


if __name__ == "__main__":
    unittest.main()
