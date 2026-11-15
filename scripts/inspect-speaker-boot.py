#!/usr/bin/env python3
"""Inspect speaker boot ordering without starting streams or changing services.

Print diagnostic JSON only; this is not selfcheck/verify and never authorizes
playback. ERR is kept as the absolute lifetime count, not adjusted for starts.
Run as the seat user, or pass --uid when reading privileged debugfs as root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "54c911c7a7098af91f90375ae1b8d09f8588c79a"
HELPER_HASHES = {
    "gen.py": "19b3c8c505a6ed26abe35f6301ca6b98bf58222891b49c623b5f527c87e978e9",
    "speakerguardd-session": "d8f89022be924016eec4a2a06ea27f898d7209cd2ef2ea39a794b86f554f2144",
    "speakerguardd-selfcheck": "c44d65cfe5c78132db61efc6fc38de56ccd38a983fa75675370404dbacf8625f",
}
JOURNAL_PATTERN = (
    "^protection granted$|Speaker protection|Speaker wiring|speakerguardd-session:|"
    "speakerguardd-selfcheck:|speakerguardd-provision: graph|"
    "follower delay|underrun|overrun|xrun|PipeWire Multimedia Service|"
    "Multimedia Service Session Manager"
)
MISSING_CAUSAL_EVIDENCE = [
    "Monotonic timestamp of the first speaker ERR increment (the lifetime count has no event time).",
    "Emitting layer and reason for that increment: ALSA PCM xrun, graph deadline, or another node error.",
    "PCM start/stop and graph link-completion/driver-handover order at that increment.",
]


def read_command(argv):
    """Read a bounded command result; preserve collection failures as evidence."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": str(exc)}, ""
    receipt = {
        "argv": argv,
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr": result.stderr.strip(),
    }
    return receipt, result.stdout if result.returncode == 0 else ""


def parse_journal(text):
    """Parse journalctl JSONL; missing/malformed monotonic time is not guessed."""
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        stamp = record.get("__MONOTONIC_TIMESTAMP")
        if not isinstance(stamp, str) or not stamp.isdecimal():
            raise ValueError("journal record lacks a monotonic timestamp")
        message = record.get("MESSAGE")
        if not isinstance(message, str):
            raise ValueError("journal record lacks a text message")
        records.append({
            "mono_ns": int(stamp) * 1000,
            "boot_id": record.get("_BOOT_ID"),
            "identifier": record.get("SYSLOG_IDENTIFIER"),
            "pid": record.get("_PID"),
            "message": message,
        })
    return sorted(records, key=lambda item: item["mono_ns"])


def parse_pw_top(text):
    """Keep the last statistics callback, ignoring pw-top's initial C/zero rows."""
    nodes = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[0] not in {"R", "I", "S", "E"}:
            continue
        name = fields[-1]
        if not (name.startswith("sgv2.") or name == "alsa_output.platform-sound.RawSpeakers"):
            continue
        try:
            row = {"id": int(fields[1]), "state": fields[0], "err": int(fields[8])}
        except ValueError:
            continue
        nodes[name] = row
    return nodes


def event_kind(record):
    message = record["message"]
    identifier = record["identifier"]
    if identifier == "kernel" and message.startswith("snd-soc-macaudio sound: Speaker protection granted"):
        return "kernel_grant"
    if identifier == "kernel" and "Speaker protection withdrawn:" in message:
        return "kernel_withdrawal"
    if "speakerguardd-session: granted=" in message:
        return "helper_state"
    if identifier == "speakerguardd" and message == "protection granted":
        return "supervisor_grant"
    if "follower delay:" in message and "resync" in message:
        return "sense_resync" if "DEV=2c:" in message else "follower_resync"
    for prefix, kind in (
        ("Finished Speaker protection graph for", "graph_ready"),
        ("Started PipeWire Multimedia Service", "pipewire_started"),
        ("Started Speaker protection session helper", "helper_started"),
        ("Starting Speaker protection supervisor", "supervisor_starting"),
        ("Started Speaker protection supervisor", "supervisor_started"),
        ("speakerguardd-selfcheck: holding", "switch_test_hold"),
        ("speakerguardd-selfcheck: released", "switch_test_release"),
        ("speakerguardd-selfcheck: passed on", "selfcheck_pass"),
        ("speakerguardd-provision: graph for", "graph_generated"),
    ):
        if message.startswith(prefix):
            return kind
    return "related_journal"


def summarize(records, nodes, refused_count):
    """Describe observations, never infer an ERR event time or qualification."""
    events = [dict(record, kind=event_kind(record)) for record in records]
    boot_ids = sorted({record["boot_id"] for record in records if record["boot_id"]})
    if len(boot_ids) > 1:
        raise ValueError("journal spans multiple boots")
    if refused_count is not None and (type(refused_count) is not int or refused_count < 0):
        raise ValueError("refused-start count must be a nonnegative integer or unavailable")
    grants = []
    for event in events:
        if event["kind"] == "kernel_grant":
            match = re.search(r"\((\d+) speaker starts? refused before\)", event["message"])
            grants.append({"mono_ns": event["mono_ns"], "refused_before_grant": int(match[1]) if match else 0})
    return {
        "schema": 1,
        "purpose": "speaker-boot-diagnostic-only",
        "playback_authorized": False,
        "source_fix_evidence": "insufficient",
        "boot_id": boot_ids[0] if boot_ids else None,
        "events": events,
        "kernel_grants": grants,
        "speaker_starts_refused": refused_count,
        "ungranted_start_evidence": (
            "not observed by the kernel counter" if refused_count == 0 else
            "observed by the kernel counter" if refused_count is not None else
            "kernel counter unavailable"
        ),
        "nodes": nodes,
        "untimed_lifetime_errors": {name: row["err"] for name, row in nodes.items() if row["err"] > 0},
        "missing_causal_evidence": list(MISSING_CAUSAL_EVIDENCE),
        "interpretation": "A follower resync journal entry is not an ERR increment timestamp. No counter is reset, subtracted, or ignored.",
    }


def helper_identity(source_root):
    """Compare only these helpers; not a replacement for installer attestation."""
    files = {}
    for name, expected in HELPER_HASHES.items():
        row = {"expected_sha256": expected}
        for label, path in (
            ("source", source_root / "packaging/session" / name),
            ("installed", Path("/usr/libexec/speakerguardd") / name),
        ):
            try:
                row[label + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                row[label + "_error"] = str(exc)
        row["matches_expected"] = all(row.get(label + "_sha256") == expected for label in ("source", "installed"))
        files[name] = row
    return {"source_commit": SOURCE_COMMIT, "scope": "three session helpers only; original source/native collection/rendering-law attestation remains required", "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", type=int, default=os.getuid(), help="seat user's uid for read-only PipeWire statistics")
    parser.add_argument("--source-root", type=Path, default=ROOT / "work/build/speaker-bringup" / ("speakerguardd-" + SOURCE_COMMIT))
    parser.add_argument("--refused-starts", type=Path, default=Path("/sys/kernel/debug/asoc/MacBook Pro J716/speaker_starts_refused"))
    args = parser.parse_args()
    if args.uid == 0:
        parser.error("pass the non-root seat user's --uid; do not query root's PipeWire")
    if os.getuid() not in (0, args.uid):
        parser.error("run as the seat user or root; do not query another user's graph")
    started = time.monotonic_ns()
    journal_receipt, journal = read_command(["journalctl", "-b", "--no-pager", "-o", "json", "--grep", JOURNAL_PATTERN])
    prefix = [] if os.getuid() == args.uid else ["runuser", "-u", pwd.getpwuid(args.uid).pw_name, "--"]
    graph_receipt, top = read_command(prefix + ["env", f"XDG_RUNTIME_DIR=/run/user/{args.uid}", "pw-top", "-b", "-n", "2"])
    counter_error = None
    try:
        refused_count = int(args.refused_starts.read_text().strip())
    except (OSError, ValueError) as exc:
        refused_count, counter_error = None, str(exc)
    result = summarize(parse_journal(journal), parse_pw_top(top), refused_count)
    result.update({
        "kernel": os.uname().release,
        "collection_interval_mono_ns": [started, time.monotonic_ns()],
        "collection": {"journal": journal_receipt, "pw_top": graph_receipt, "refused_counter_path": str(args.refused_starts), "refused_counter_error": counter_error},
        "helper_identity": helper_identity(args.source_root),
    })
    print(json.dumps(result, indent=2, sort_keys=True))
    # Missing observations remain a collection error, never a passing verify.
    return 0 if journal and result["nodes"] and refused_count is not None else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError) as exc:
        print(f"speaker boot evidence: {exc}", file=sys.stderr)
        sys.exit(1)
