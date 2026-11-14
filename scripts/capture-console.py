#!/usr/bin/env python3
"""Capture a local KIS/DockChannel PTY without sending bytes to the target."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, default=Path("/dev/m1n1"))
    parser.add_argument("--output", type=Path, required=True, help="new local log; never commit it")
    parser.add_argument("--seconds", type=float, default=120, help="positive capture duration")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    # kisd provides a PTY, not a hardware UART requiring baud/control-line setup.
    # Keep its raw mode intact and open read-only: no proxy commands or writes.
    descriptor = os.open(args.device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    captured = 0
    try:
        with args.output.open("xb") as destination, selectors.DefaultSelector() as selector:
            os.fchmod(destination.fileno(), 0o600)
            selector.register(descriptor, selectors.EVENT_READ)
            deadline = time.monotonic() + args.seconds
            while not stopping and time.monotonic() < deadline:
                if not selector.select(timeout=min(0.25, max(0, deadline - time.monotonic()))):
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                destination.write(chunk)
                destination.flush()
                captured += len(chunk)
            os.fsync(destination.fileno())
    finally:
        os.close(descriptor)
    print(f"Captured {captured} bytes to {args.output}; no target bytes sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
