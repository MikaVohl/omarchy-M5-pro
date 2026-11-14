#!/usr/bin/env python3
"""Collect private, read-only G17 recovery evidence; never run GPU work or change policy.

Journal times describe the requested incident. Proc/sys/debugfs files are explicitly
labelled as current snapshots, not as evidence of the historical instant. No process
memory, full environment, core dump, or display capture is collected. Kernel logs
may contain device/network identifiers; review the private bundle before sharing.
The existing debugfs host-memory views need root; missing access is recorded rather
than mounting debugfs, escalating privileges, or treating absence as a clean result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEBUGFS = Path("/sys/kernel/debug/aurora-g17")
# These views read retained host memory. Do not collect power/firmware_reports:
# their readers also access MMIO, even though they do not power up the GPU.
HOST_VIEWS = ("capacity", "firmware_log", "hardware_data")
GPU_LIBRARIES = ("/opt/mesa-j716s/", "libvulkan", "libEGL", "libgbm", "libgallium")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Capture:
    def __init__(self, destination):
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.destination = destination
        self.files = {}
        self.errors = []

    def save(self, name, content):
        if isinstance(content, str):
            content = content.encode()
        with (self.destination / name).open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
        self.files[name] = {"bytes": len(content),
                            "sha256": hashlib.sha256(content).hexdigest()}

    def text(self, path):
        try:
            return path.read_text(errors="replace")
        except OSError as error:
            self.errors.append({"source": str(path), "error": str(error)})
            return None

    def copy(self, path, name):
        content = self.text(path)
        if content is not None:
            self.save(name, content)

    def command(self, name, arguments):
        try:
            result = subprocess.run(arguments, capture_output=True, timeout=30,
                                    env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
        except (OSError, subprocess.TimeoutExpired) as error:
            self.errors.append({"source": arguments, "error": str(error)})
            return
        self.save(name, result.stdout)
        if result.stderr:
            self.save(name + ".stderr", result.stderr)
        if result.returncode:
            self.errors.append({"source": arguments, "returncode": result.returncode})

    def process(self, pid):
        proc = Path("/proc") / str(pid)
        before = self.text(proc / "stat")
        if before is None:
            return
        # A PID can exit or be reused during capture; record the start-time stat
        # on both sides. Consumers must not attribute raced samples to one client.
        self.save(f"current-process-{pid}-stat-before.txt", before)
        self.copy(proc / "comm", f"current-process-{pid}-comm.txt")
        maps = self.text(proc / "maps")
        if maps is not None:
            self.save(f"current-process-{pid}-gpu-maps.txt", "".join(
                line + "\n" for line in maps.splitlines()
                if any(library in line for library in GPU_LIBRARIES)))
        try:
            descriptors = sorted((proc / "fdinfo").iterdir(), key=lambda path: int(path.name))
        except OSError as error:
            self.errors.append({"source": str(proc / "fdinfo"), "error": str(error)})
            descriptors = []
        for descriptor in descriptors:
            info = self.text(descriptor)
            if info is not None and any(line.startswith("drm-driver:")
                                        for line in info.splitlines()):
                self.save(f"current-process-{pid}-drm-fd-{descriptor.name}.txt", info)
        self.copy(proc / "stat", f"current-process-{pid}-stat-after.txt")


def compositor_pids(capture):
    pids = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if (proc / "comm").read_text().strip() == "Hyprland":
                pids.append(int(proc.name))
        except FileNotFoundError:
            pass  # Process exited while discovering the current compositor.
        except OSError as error:
            capture.errors.append({"source": str(proc / "comm"), "error": str(error)})
    return sorted(pids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="journalctl incident window start, with timezone")
    parser.add_argument("--until", required=True, help="journalctl incident window end, with timezone")
    parser.add_argument("--boot", help="incident boot ID; defaults to the current boot ID")
    parser.add_argument("--pid", type=int, action="append", help="current GPU client PID; default: Hyprland")
    parser.add_argument("--output", type=Path, required=True, help="new private directory; never commit it")
    args = parser.parse_args()
    if args.pid and any(pid <= 0 for pid in args.pid):
        parser.error("--pid must be positive")
    try:
        times = [datetime.fromisoformat(value) for value in (args.since, args.until)]
        if any(value.tzinfo is None for value in times) or times[1] <= times[0]:
            raise ValueError("incident times need explicit offsets and a positive window")
        since, until = ("@" + str(int(value.timestamp())) for value in times)
    except ValueError as error:
        parser.error(str(error))
    os.umask(0o077)
    capture = Capture(args.output)
    started = utc_now()
    boot_id = capture.text(Path("/proc/sys/kernel/random/boot_id"))
    incident_boot = args.boot or (boot_id.strip() if boot_id else None)
    if incident_boot is None:
        parser.error("cannot determine current boot ID; supply --boot")
    try:
        incident_boot = uuid.UUID(incident_boot).hex
    except ValueError:
        parser.error("--boot must be an actual boot UUID")
    window = ["--boot=" + incident_boot, "--since", since, "--until", until,
              "--no-pager", "--quiet"]
    capture.command("incident-kernel.jsonl", ["journalctl", "-k", *window, "-o", "json"])
    capture.command("incident-hyprland.jsonl", ["journalctl", *window, "_COMM=Hyprland", "-o", "json"])
    capture.command("incident-coredumps.txt", ["coredumpctl", "list", "--no-pager",
                    "--since", since, "--until", until,
                    "_BOOT_ID=" + incident_boot])
    capture.command("incident-gpu-startup.jsonl", ["journalctl", "-k", "--boot=" + incident_boot,
                    "--no-pager", "--quiet", "-o", "json", "--grep",
                    "GPU firmware|GPU memory limits|GPU idle power-off|M5.*initdata|firmware scheduling pause timeout"])
    for path, name in ((Path("/proc/version"), "current-kernel-version.txt"),
                       (Path("/proc/meminfo"), "current-meminfo.txt"),
                       (Path("/proc/pressure/memory"), "current-memory-pressure.txt"),
                       (Path("/opt/mesa-j716s/MESA_COMMIT"), "current-mesa-commit.txt"),
                       (ROOT / "profiles/j716s.json", "pinned-profile.json")):
        capture.copy(path, name)
    command_line = capture.text(Path("/proc/cmdline"))
    if command_line is not None:
        capture.save("current-gpu-command-line.txt", "\n".join(
            token for token in command_line.split()
            if token.startswith(("aurora.", "asahi."))) + "\n")
    for view in HOST_VIEWS:
        capture.copy(DEBUGFS / view, "current-g17-" + view + ".txt")
    for module in ("aurora", "asahi"):
        directory = Path("/sys/module") / module / "parameters"
        try:
            parameters = sorted(directory.iterdir())
        except OSError as error:
            capture.errors.append({"source": str(directory), "error": str(error)})
            continue
        for parameter in parameters:
            capture.copy(parameter, f"current-{module}-parameter-{parameter.name}.txt")
    pids = sorted(set(args.pid)) if args.pid else compositor_pids(capture)
    for pid in pids:
        capture.process(pid)
    manifest = {"schema": 1, "capture_started_utc": started, "capture_finished_utc": utc_now(),
                "incident_window": {"since": args.since, "until": args.until,
                                    "boot_id": incident_boot},
                "current_snapshot_boot_id": boot_id.strip() if boot_id else None,
                "current_snapshot_pids": pids,
                "limitations": ["Current snapshots were not taken at the incident instant.",
                                "Retained queues identify owners, not culpability.",
                                "Missing sources or errors do not mean no fault occurred.",
                                "No GPU workload, ioctl, policy write or MMIO probe was issued."],
                "files": capture.files, "errors": capture.errors}
    capture.save("capture-manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Private GPU evidence: {args.output}; {len(capture.errors)} unavailable/failed sources.")
    print("No recovery fix or stability qualification is implied.")
    return 1 if capture.errors else 0


if __name__ == "__main__":
    sys.exit(main())
