#!/usr/bin/env python3
"""Passively collect Bluetooth synchronous-link protocol metadata, never microphone audio.

Run only during an independently authorized session; this tool does not connect,
pair, select a profile, send HCI commands, or start playback/recording. CAP_NET_RAW
(or sudo) and Linux Python 3.14+ are needed. A mandatory, locked kernel socket
filter is installed BEFORE binding: ACL, ISO, vendor, pairing/key and unrelated
traffic is dropped; SCO packets are truncated to the monitor + HCI SCO headers.
Only allowlisted control fields and aggregate SCO header counts reach the private
JSON bundle. Addresses are omitted. No raw packet or audio payload is saved.

Example (does not initiate any Bluetooth activity):
  sudo -n python3 -B scripts/capture-bluetooth-sco.py --controller hci0 \
    --duration 60 --output /tmp/j716s-sco-private

Monitor TX precedes the driver send callback, so TX counts do NOT prove delivery.
RX counts only cover frames the driver submitted to HCI, not unopened DMA rings.
The monitor cannot recover firmware SCO framing, RX attributes or ring credits.
"""
import argparse
from collections import Counter
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HCI_CHANNEL_MONITOR = 2
HCI_DEV_NONE = 0xffff
SO_ATTACH_FILTER = 26
SO_LOCK_FILTER = 44
MAX_CONTROL_RECORDS = 10000
# Standard HCI layouts: include/net/bluetooth/hci.h; monitor header: hci_mon.h.
COMMANDS = {
    0x0406: ("disconnect", 3),
    0x0407: ("add_sco_connection", 4),
    0x0428: ("setup_synchronous_connection", 17),
    0x0429: ("accept_synchronous_connection", 21),
    0x042a: ("reject_synchronous_connection", 7),
    0x043d: ("enhanced_setup_synchronous_connection", 59),
    0x043e: ("enhanced_accept_synchronous_connection", 63),
    0x0c25: ("read_voice_setting", 0),
    0x0c26: ("write_voice_setting", 2),
    0x0c33: ("host_buffer_size", 7),
    0x1005: ("read_buffer_size", 0),
}
EVENTS = {0x03, 0x04, 0x05, 0x13, 0x2c, 0x2d}


class ProtocolError(ValueError):
    pass


def swapped(value):
    # Classic BPF absolute halfword loads are big endian; HCI fields are LE.
    return ((value & 0xff) << 8) | (value >> 8)


def monitor_filter(index):
    """Classic BPF whitelist, with SCO snaplen 9 (zero audio bytes).

    Small forward-only assembler avoids hand-maintained jump offsets. Constants
    are Linux UAPI BPF_LD/H/B/ABS, BPF_JMP/JEQ/K and BPF_RET/K encodings.
    """
    rows = []
    labels = {}

    def label(name):
        labels[name] = len(rows)

    def load(offset, byte=False):
        rows.append((0x30 if byte else 0x28, 0, 0, offset))

    def equal(value, target):
        rows.append((0x15, target, 0, value))

    load(2)
    equal(swapped(index), "index_ok")
    rows.append((0x06, 0, 0, 0))
    label("index_ok")
    load(0)
    equal(swapped(2), "command")
    equal(swapped(3), "event")
    equal(swapped(6), "sco")
    equal(swapped(7), "sco")
    rows.append((0x06, 0, 0, 0))
    label("command")
    load(6)
    for opcode in COMMANDS:
        equal(swapped(opcode), "control")
    rows.append((0x06, 0, 0, 0))
    label("event")
    load(6, byte=True)
    for event in sorted(EVENTS):
        equal(event, "control")
    equal(0x0e, "complete")
    equal(0x0f, "status")
    rows.append((0x06, 0, 0, 0))
    label("complete")
    load(9)
    for opcode in COMMANDS:
        equal(swapped(opcode), "control")
    rows.append((0x06, 0, 0, 0))
    label("status")
    load(10)
    for opcode in COMMANDS:
        equal(swapped(opcode), "control")
    rows.append((0x06, 0, 0, 0))
    label("sco")
    rows.append((0x06, 0, 0, 9))
    label("control")
    rows.append((0x06, 0, 0, 264))  # 6-byte monitor + 3-byte HCI + 255 parameters.
    program = []
    for position, (code, yes, no, value) in enumerate(rows):
        if isinstance(yes, str):
            yes = labels[yes] - position - 1
            if not 0 <= yes <= 255:
                raise ValueError("socket filter jump exceeds classic BPF range")
        program.append((code, yes, no, value))
    return program


class SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]


def attach_filter(monitor, index):
    rows = monitor_filter(index)
    instructions = (SockFilter * len(rows))(*(SockFilter(*row) for row in rows))
    program = SockFprog(len(rows), instructions)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.setsockopt.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_uint]
    libc.setsockopt.restype = ctypes.c_int
    if libc.setsockopt(monitor.fileno(), socket.SOL_SOCKET, SO_ATTACH_FILTER,
                       ctypes.byref(program), ctypes.sizeof(program)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "mandatory privacy filter: " + os.strerror(error))
    monitor.setsockopt(socket.SOL_SOCKET, SO_LOCK_FILTER, 1)


def open_monitor(index):
    monitor = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        monitor.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        attach_filter(monitor, index)
        # Unlike USER/RAW, MONITOR does not acquire or power the controller.
        monitor.bind((HCI_DEV_NONE, HCI_CHANNEL_MONITOR))
        return monitor
    except BaseException:
        monitor.close()
        raise


def u16(data, offset=0):
    return struct.unpack_from("<H", data, offset)[0]


def u32(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


def exact(data, size, name):
    if len(data) != size:
        raise ProtocolError(name + " has an unexpected parameter length")


def coding_format(data, offset):
    return {"id": data[offset], "company_id": u16(data, offset + 1),
            "vendor_codec_id": u16(data, offset + 3)}


def command_fields(opcode, data):
    name, size = COMMANDS[opcode]
    exact(data, size, name)
    fields = {"command": name, "opcode": opcode}
    if opcode in (0x0406, 0x0407, 0x0428, 0x043d):
        fields["handle"] = u16(data) & 0x0fff
    if opcode == 0x0406:
        fields["reason"] = data[2]
    elif opcode == 0x0407:
        fields["packet_type"] = u16(data, 2)
    elif opcode in (0x0428, 0x0429):
        offset = 2 if opcode == 0x0428 else 6  # Never copy peer bdaddr.
        fields.update(tx_bandwidth=u32(data, offset), rx_bandwidth=u32(data, offset + 4),
                      max_latency=u16(data, offset + 8), voice_setting=u16(data, offset + 10),
                      retransmission_effort=data[offset + 12], packet_type=u16(data, offset + 13))
    elif opcode == 0x042a:
        fields["reason"] = data[6]
    elif opcode in (0x043d, 0x043e):
        offset = 2 if opcode == 0x043d else 6
        fields.update(tx_bandwidth=u32(data, offset), rx_bandwidth=u32(data, offset + 4),
                      tx_codec_frame_size=u16(data, offset + 18),
                      rx_codec_frame_size=u16(data, offset + 20),
                      input_bandwidth=u32(data, offset + 22), output_bandwidth=u32(data, offset + 26),
                      input_coded_data_size=u16(data, offset + 40),
                      output_coded_data_size=u16(data, offset + 42),
                      input_pcm_format=data[offset + 44], output_pcm_format=data[offset + 45],
                      input_sample_msb_position=data[offset + 46],
                      output_sample_msb_position=data[offset + 47],
                      input_data_path=data[offset + 48], output_data_path=data[offset + 49],
                      input_transport_unit_size=data[offset + 50],
                      output_transport_unit_size=data[offset + 51],
                      max_latency=u16(data, offset + 52), packet_type=u16(data, offset + 54),
                      retransmission_effort=data[offset + 56])
        for direction, relative in (("tx", 8), ("rx", 13), ("input", 30), ("output", 35)):
            fields[direction + "_coding_format"] = coding_format(data, offset + relative)
    elif opcode == 0x0c26:
        fields["voice_setting"] = u16(data)
    elif opcode == 0x0c33:
        fields.update(acl_mtu=u16(data), sco_mtu=data[2],
                      acl_max_packets=u16(data, 3), sco_max_packets=u16(data, 5))
    return fields


def event_fields(event, data):
    if event == 0x0f:
        exact(data, 4, "command_status")
        opcode = u16(data, 2)
        if opcode not in COMMANDS:
            return None
        return {"event": "command_status", "opcode": opcode,
                "command": COMMANDS[opcode][0], "status": data[0], "command_credits": data[1]}
    if event == 0x0e:
        if len(data) < 4:
            raise ProtocolError("command_complete is missing its status")
        opcode = u16(data, 1)
        if opcode not in COMMANDS:
            return None
        result = data[3:]
        fields = {"event": "command_complete", "opcode": opcode,
                  "command": COMMANDS[opcode][0], "status": result[0], "command_credits": data[0]}
        if result[0] == 0 and opcode == 0x1005:
            exact(result, 8, "read_buffer_size result")
            fields.update(acl_mtu=u16(result, 1), sco_mtu=result[3],
                          acl_max_packets=u16(result, 4), sco_max_packets=u16(result, 6))
        elif result[0] == 0 and opcode == 0x0c25:
            exact(result, 3, "read_voice_setting result")
            fields["voice_setting"] = u16(result, 1)
        return fields
    if event == 0x03:
        exact(data, 11, "connection_complete")
        return {"event": "connection_complete", "status": data[0],
                "handle": u16(data, 1) & 0x0fff, "link_type": data[9]}
    if event == 0x04:
        exact(data, 10, "connection_request")
        return {"event": "connection_request", "link_type": data[9]}
    if event == 0x05:
        exact(data, 4, "disconnection_complete")
        return {"event": "disconnection_complete", "status": data[0],
                "handle": u16(data, 1) & 0x0fff, "reason": data[3]}
    if event == 0x13:
        if not data or len(data) != 1 + 4 * data[0]:
            raise ProtocolError("number_completed_packets has an unexpected length")
        return {"event": "number_completed_packets", "connections": [
            {"handle": u16(data, offset) & 0x0fff, "completed_packets": u16(data, offset + 2)}
            for offset in range(1, len(data), 4)]}
    if event in (0x2c, 0x2d):
        exact(data, 17 if event == 0x2c else 9, "synchronous_connection event")
        offset = 10 if event == 0x2c else 3
        fields = {"event": "synchronous_connection_complete" if event == 0x2c else "synchronous_connection_changed",
                  "status": data[0], "handle": u16(data, 1) & 0x0fff,
                  "tx_interval": data[offset], "retransmission_window": data[offset + 1],
                  "rx_packet_length": u16(data, offset + 2), "tx_packet_length": u16(data, offset + 4)}
        if event == 0x2c:
            fields.update(link_type=data[9], air_mode=data[16])
        return fields
    return None


class Decoder:
    def __init__(self, index):
        self.index = index
        self.sco = {}
        self.control_counts = Counter()

    def feed(self, packet):
        if len(packet) < 6:
            raise ProtocolError("short monitor header")
        kind, index, length = struct.unpack_from("<HHH", packet)
        if index != self.index:
            return None
        if kind in (6, 7):
            # A payload reaching this decoder means the mandatory filter failed.
            exact(packet, 9, "privacy-filtered SCO header")
            handle_flags = u16(packet, 6)
            payload_length = packet[8]
            if length != 3 + payload_length:
                raise ProtocolError("SCO header and monitor lengths disagree")
            direction = "tx_requested" if kind == 6 else "rx_delivered_to_hci"
            key = (direction, handle_flags & 0x0fff)
            row = self.sco.setdefault(key, {"direction": direction, "handle": key[1],
                                           "packets": 0, "declared_payload_bytes": 0,
                                           "packet_status_counts": [0, 0, 0, 0]})
            row["packets"] += 1
            row["declared_payload_bytes"] += payload_length
            row["packet_status_counts"][(handle_flags >> 12) & 3] += 1
            return None
        if kind not in (2, 3):
            return None
        exact(packet, 6 + length, "control monitor frame")
        data = packet[6:]
        if kind == 2:
            if len(data) < 3 or len(data) != 3 + data[2]:
                raise ProtocolError("HCI command length mismatch")
            opcode = u16(data)
            fields = command_fields(opcode, data[3:]) if opcode in COMMANDS else None
        else:
            if len(data) < 2 or len(data) != 2 + data[1]:
                raise ProtocolError("HCI event length mismatch")
            fields = event_fields(data[0], data[2:])
        if fields is not None:
            self.control_counts[fields.get("event", "command:" + fields.get("command", ""))] += 1
        return fields


def private_write(directory, name, content):
    if isinstance(content, str):
        content = content.encode()
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
    return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def capture(monitor, directory, index, duration, context):
    decoder = Decoder(index)
    started_utc = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    deadline = started + duration
    reason = "duration_elapsed"
    errors = []
    count = 0
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    descriptor = os.open(directory / "control.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "wb") as stream:
        try:
            while time.monotonic() < deadline:
                monitor.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    packet, _, flags, _ = monitor.recvmsg(264)
                except socket.timeout:
                    break
                if flags & socket.MSG_TRUNC:
                    raise ProtocolError("unexpected control truncation; capture is incomplete")
                fields = decoder.feed(packet)
                if fields is None:
                    continue
                fields["elapsed_seconds"] = round(time.monotonic() - started, 6)
                content = (json.dumps(fields, sort_keys=True) + "\n").encode()
                stream.write(content)
                digest.update(content)
                size += len(content)
                count += 1
                if count >= MAX_CONTROL_RECORDS:
                    reason = "control_record_limit"
                    break
        except KeyboardInterrupt:
            reason = "interrupted"
        except (OSError, ProtocolError) as error:
            reason = "capture_error"
            errors.append(str(error))
    summary = {
        "schema": 1, "started_utc": started_utc,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "requested_duration_seconds": duration, "stop_reason": reason,
        "context": context, "controller": "hci" + str(index),
        "privacy": {"kernel_filter_installed_before_bind": True, "kernel_filter_locked": True,
                    "sco_snaplen_bytes": 9, "audio_payload_bytes_saved": 0,
                    "acl_iso_vendor_key_packets": "dropped_in_kernel", "peer_addresses_saved": False},
        "control_records": count, "control_counts": dict(decoder.control_counts),
        "sco_headers": list(decoder.sco.values()), "errors": errors,
        "files": {"control.jsonl": {"bytes": size, "sha256": digest.hexdigest()}},
        "limitations": [
            "No profile, pairing, controller command, playback or recording was initiated.",
            "TX monitor packets precede the driver send callback and do not prove wire delivery.",
            "Elapsed event timestamps describe userspace receipt, not controller/air timestamps.",
            "RX covers HCI-delivered frames only; unopened SCO DMA resources remain invisible.",
            "Kernel allocation/socket-queue losses are not reported by this HCI monitor API; absence is not proof of no traffic.",
            "HFP RFCOMM codec negotiation is not collected: all ACL payload is dropped.",
            "Voice setting/air coding is not alone proof of mSBC codec negotiation.",
            "Monitor metadata cannot qualify firmware SCO framing, attributes, credits or microphone support.",
            "If no relevant session occurred during this window, this is an idle capture, not a SCO repro.",
        ],
    }
    private_write(directory, "summary.json", json.dumps(summary, indent=2) + "\n")
    print(f"Private SCO protocol metadata: {directory}; {count} control records; {reason}.")
    print("No audio recorded or Bluetooth state changed; microphone support remains unqualified.")
    return 0 if reason == "duration_elapsed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", default="hci0", help="existing HCI controller; no scan or command is sent")
    parser.add_argument("--duration", type=int, default=60, help="passive interval, 1..3600 seconds")
    parser.add_argument("--output", type=Path, required=True, help="new private directory outside this repository")
    args = parser.parse_args()
    match = re.fullmatch(r"hci([0-9]+)", args.controller)
    if not match or not 0 <= int(match[1]) < HCI_DEV_NONE:
        parser.error("--controller must be hci0..hci65534")
    if not 1 <= args.duration <= 3600:
        parser.error("--duration must be 1..3600")
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("private protocol metadata must be saved outside the repository")
    if output.exists() or not output.parent.is_dir():
        parser.error("--output must be a new directory with an existing parent")
    if sys.version_info < (3, 14):
        parser.error("Python 3.14+ is required for the HCI monitor channel address")
    device = Path("/sys/class/bluetooth") / args.controller
    if not device.exists():
        parser.error("requested controller is not present; no controller will be created")
    driver = (device / "device/driver").resolve().name
    context = {"kernel_release": os.uname().release, "driver": driver}
    os.umask(0o077)
    try:
        with open_monitor(int(match[1])) as monitor:
            return capture(monitor, output, int(match[1]), args.duration, context)
    except OSError as error:
        print("Capture refused/failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
