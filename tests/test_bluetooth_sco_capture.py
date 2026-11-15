"""Offline SCO protocol/privacy guards; no Bluetooth connection or microphone use."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import stat
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bluetooth_sco_capture", ROOT / "scripts/capture-bluetooth-sco.py")
sco = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sco)


def monitor(kind, payload, index=0):
    return struct.pack("<HHH", kind, index, len(payload)) + payload


def command(opcode, parameters=b"", index=0):
    return monitor(2, struct.pack("<HB", opcode, len(parameters)) + parameters, index)


def event(code, parameters=b"", index=0):
    return monitor(3, bytes((code, len(parameters))) + parameters, index)


def filtered(program, packet):
    """Execute the emitted classic BPF subset, including out-of-bounds drop."""
    accumulator = 0
    position = 0
    while position < len(program):
        code, yes, no, value = program[position]
        if code in (0x28, 0x30):
            size = 2 if code == 0x28 else 1
            if value + size > len(packet):
                return b""
            accumulator = int.from_bytes(packet[value:value + size], "big")
        elif code == 0x15:
            position += yes if accumulator == value else no
        elif code == 0x06:
            return packet[:value]
        else:
            raise AssertionError("unimplemented BPF instruction")
        position += 1
    raise AssertionError("filter did not return")


class PrivacyFilterTests(unittest.TestCase):
    def test_sco_audio_is_truncated_for_each_controller_and_direction(self):
        for index in (0, 513, 65534):
            program = sco.monitor_filter(index)
            for kind in (6, 7):
                packet = monitor(kind, struct.pack("<HB", 0x2042, 13) + b"PRIVATE-AUDIO", index)
                self.assertEqual(filtered(program, packet), packet[:9])
                self.assertEqual(filtered(sco.monitor_filter(index + 1), packet), b"")

    def test_acl_iso_vendor_and_key_traffic_is_dropped_in_kernel(self):
        program = sco.monitor_filter(0)
        for kind in (0, 1, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21):
            self.assertEqual(filtered(program, monitor(kind, b"PRIVATE PAYLOAD")), b"")
        for opcode in (0x040b, 0x040d, 0x0c57, 0x1009, 0xfc06, 0xffa3):
            self.assertEqual(filtered(program, command(opcode, b"PRIVATE")), b"")
            self.assertEqual(filtered(program, event(0x0e, struct.pack("<BH", 1, opcode) + b"PRIVATE")), b"")
            self.assertEqual(filtered(program, event(0x0f, struct.pack("<BBH", 0, 1, opcode))), b"")
        for code in (0x15, 0x16, 0x18, 0x31, 0x32, 0x33, 0x3e, 0xff):
            self.assertEqual(filtered(program, event(code, b"PRIVATE")), b"")

    def test_all_allowlisted_commands_status_and_completions_pass(self):
        program = sco.monitor_filter(0)
        for opcode, (_, size) in sco.COMMANDS.items():
            packets = (command(opcode, bytes(size)),
                       event(0x0f, struct.pack("<BBH", 0, 1, opcode)),
                       event(0x0e, struct.pack("<BH", 1, opcode) + b"\x00"))
            for packet in packets:
                self.assertEqual(filtered(program, packet), packet)
        for code in sco.EVENTS:
            packet = event(code, bytes(17))
            self.assertEqual(filtered(program, packet), packet)

    def test_short_headers_are_dropped(self):
        program = sco.monitor_filter(0)
        for packet in (b"", b"\x02", b"\x02\x00\x00", monitor(2, b""), monitor(3, b"")):
            self.assertEqual(filtered(program, packet), b"")

    def test_real_linux_socket_filter_truncates_and_drops_synthetic_packets(self):
        # AF_UNIX datagrams exercise the actual kernel BPF verifier/filter path,
        # not HCI, radio traffic or audio hardware; no elevated privilege needed.
        receive, send = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(receive.close)
        self.addCleanup(send.close)
        receive.settimeout(0.05)
        sco.attach_filter(receive, 0)
        packet = monitor(7, struct.pack("<HB", 0x42, 13) + b"PRIVATE-AUDIO")
        send.send(packet)
        self.assertEqual(receive.recv(264), packet[:9])
        for packet in (monitor(5, b"PRIVATE"), command(0xfc06, b"PRIVATE")):
            send.send(packet)
            with self.assertRaises(socket.timeout):
                receive.recv(264)
        packet = command(0x0428, struct.pack("<HIIHHBH", 0x42, 8000, 8000, 13, 0x63, 2, 0x388))
        send.send(packet)
        self.assertEqual(receive.recv(264), packet)
        with self.assertRaises(OSError):
            receive.setsockopt(socket.SOL_SOCKET, 27, 0)  # SO_DETACH_FILTER is locked.

    def test_privacy_filter_failure_refuses_before_monitor_bind(self):
        monitor_socket = MagicMock()
        with patch.object(sco.socket, "socket", return_value=monitor_socket), \
             patch.object(sco, "attach_filter", side_effect=OSError("filter refused")):
            with self.assertRaises(OSError):
                sco.open_monitor(0)
        monitor_socket.bind.assert_not_called()
        monitor_socket.close.assert_called_once()

    def test_only_monitor_channel_is_bound_after_filter(self):
        monitor_socket = MagicMock()
        order = []
        monitor_socket.bind.side_effect = lambda address: order.append(("bind", address))
        with patch.object(sco.socket, "socket", return_value=monitor_socket), \
             patch.object(sco, "attach_filter", side_effect=lambda sock, index: order.append(("filter", index))):
            self.assertIs(sco.open_monitor(513), monitor_socket)
        self.assertEqual(order, [("filter", 513), ("bind", (0xffff, 2))])
        monitor_socket.send.assert_not_called()
        monitor_socket.sendmsg.assert_not_called()


class ProtocolDecoderTests(unittest.TestCase):
    def setUp(self):
        self.decoder = sco.Decoder(0)

    def test_setup_and_accept_decode_bandwidth_voice_and_packet_type_without_address(self):
        parameters = struct.pack("<HIIHHBH", 0x42, 8000, 8000, 13, 0x63, 2, 0x388)
        setup = self.decoder.feed(command(0x0428, parameters))
        self.assertEqual(setup["handle"], 0x42)
        self.assertEqual(setup["voice_setting"], 0x63)
        self.assertEqual(setup["packet_type"], 0x388)
        self.assertEqual(setup["max_latency"], 13)
        accept = self.decoder.feed(command(0x0429, b"SECRET" + parameters[2:]))
        self.assertEqual(accept["tx_bandwidth"], 8000)
        self.assertNotIn("handle", accept)
        self.assertNotIn("SECRET", json.dumps(accept))

    def test_enhanced_setup_and_accept_decode_data_paths_and_formats(self):
        # Distinct values for every field catch accidental shifted layouts.
        body = struct.pack("<II", 8000, 16000)
        body += struct.pack("<BHHBHH", 5, 1, 2, 5, 3, 4)
        body += struct.pack("<HHII", 60, 120, 8000, 16000)
        body += struct.pack("<BHHBHH", 3, 5, 6, 3, 7, 8)
        body += struct.pack("<HHBBBBBBBBHHB", 16, 24, 1, 2, 3, 4, 0, 1, 60, 120, 13, 0x388, 2)
        for opcode, prefix in ((0x043d, struct.pack("<H", 0x42)), (0x043e, b"SECRET")):
            result = self.decoder.feed(command(opcode, prefix + body))
            self.assertEqual(result["tx_coding_format"], {"id": 5, "company_id": 1, "vendor_codec_id": 2})
            self.assertEqual(result["output_coding_format"], {"id": 3, "company_id": 7, "vendor_codec_id": 8})
            self.assertEqual(result["rx_codec_frame_size"], 120)
            self.assertEqual(result["output_coded_data_size"], 24)
            self.assertEqual(result["input_data_path"], 0)
            self.assertEqual(result["output_data_path"], 1)
            self.assertEqual(result["input_transport_unit_size"], 60)
            self.assertEqual(result["output_transport_unit_size"], 120)
            self.assertEqual(result["retransmission_effort"], 2)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_sync_completion_and_change_omit_peer_address(self):
        complete = bytes((0,)) + struct.pack("<H", 0x42) + b"SECRET" + struct.pack("<BBBHHB", 2, 6, 2, 60, 60, 3)
        result = self.decoder.feed(event(0x2c, complete))
        self.assertEqual(result, {"event": "synchronous_connection_complete", "status": 0,
                                 "handle": 0x42, "link_type": 2, "tx_interval": 6,
                                 "retransmission_window": 2, "rx_packet_length": 60,
                                 "tx_packet_length": 60, "air_mode": 3})
        change = self.decoder.feed(event(0x2d, struct.pack("<BHBBHH", 0, 0x42, 12, 4, 120, 120)))
        self.assertEqual(change["rx_packet_length"], 120)
        self.assertEqual(change["tx_interval"], 12)

    def test_status_and_buffer_voice_results_keep_status_and_credits(self):
        status = self.decoder.feed(event(0x0f, struct.pack("<BBH", 0x1a, 1, 0x0428)))
        self.assertEqual(status["status"], 0x1a)
        self.assertEqual(status["command_credits"], 1)
        parameters = struct.pack("<BH", 1, 0x1005) + struct.pack("<BHBHH", 0, 1021, 60, 8, 4)
        result = self.decoder.feed(event(0x0e, parameters))
        self.assertEqual(result["sco_mtu"], 60)
        self.assertEqual(result["sco_max_packets"], 4)
        result = self.decoder.feed(event(0x0e, struct.pack("<BHBH", 1, 0x0c25, 0, 0x63)))
        self.assertEqual(result["voice_setting"], 0x63)
        failed = self.decoder.feed(event(0x0e, struct.pack("<BHB", 1, 0x1005, 1)))
        self.assertEqual(failed["status"], 1)
        self.assertNotIn("sco_mtu", failed)

    def test_sco_counts_only_headers_and_labels_tx_as_requests(self):
        for kind in (6, 7):
            for status in range(4):
                packet = monitor(kind, struct.pack("<HB", 0x42 | (status << 12), 13) + b"PRIVATE-AUDIO")
                self.assertIsNone(self.decoder.feed(filtered(sco.monitor_filter(0), packet)))
        rows = list(self.decoder.sco.values())
        self.assertEqual([row["direction"] for row in rows], ["tx_requested", "rx_delivered_to_hci"])
        for row in rows:
            self.assertEqual(row["packets"], 4)
            self.assertEqual(row["declared_payload_bytes"], 52)
            self.assertEqual(row["packet_status_counts"], [1, 1, 1, 1])
        self.assertNotIn("PRIVATE-AUDIO", json.dumps(rows))

    def test_malformed_and_unfiltered_sco_fail_without_echoing_payload(self):
        packets = (b"\x02", command(0x0428, b"short"), event(0x2c, b"short"),
                   event(0x13, b"\x02\x00"),
                   monitor(7, struct.pack("<HB", 0x42, 13) + b"PRIVATE-AUDIO"),
                   struct.pack("<HHH", 7, 0, 20) + struct.pack("<HB", 0x42, 13))
        for packet in packets:
            with self.assertRaises(sco.ProtocolError) as error:
                self.decoder.feed(packet)
            self.assertNotIn("PRIVATE-AUDIO", str(error.exception))
        self.assertFalse(self.decoder.sco)

    def test_unrelated_controller_and_commands_are_not_decoded(self):
        self.assertIsNone(self.decoder.feed(command(0x1005, index=1)))
        self.assertIsNone(self.decoder.feed(command(0xfc06, b"SECRET")))
        self.assertIsNone(self.decoder.feed(event(0x18, b"SECRET")))
        self.assertFalse(self.decoder.control_counts)


class CaptureBundleTests(unittest.TestCase):
    def test_private_bundle_contains_only_metadata_and_header_aggregates(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "private"
            monitor_socket = MagicMock()
            sync = bytes((0,)) + struct.pack("<H", 0x42) + b"SECRET" + struct.pack("<BBBHHB", 2, 6, 2, 60, 60, 3)
            audio = monitor(7, struct.pack("<HB", 0x42, 13) + b"PRIVATE-AUDIO")
            packets = [event(0x2c, sync), filtered(sco.monitor_filter(0), audio)]
            monitor_socket.recvmsg.side_effect = [(packet, [], 0, None) for packet in packets] + [socket.timeout()]
            with patch("builtins.print"):
                result = sco.capture(monitor_socket, destination, 0, 1, {"kernel_release": "fixture"})
            self.assertEqual(result, 0)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertEqual({path.name for path in destination.iterdir()}, {"control.jsonl", "summary.json"})
            for path in destination.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertNotIn(b"SECRET", path.read_bytes())
                self.assertNotIn(b"PRIVATE-AUDIO", path.read_bytes())
            content = (destination / "control.jsonl").read_bytes()
            summary = json.loads((destination / "summary.json").read_text())
            self.assertEqual(summary["privacy"]["audio_payload_bytes_saved"], 0)
            self.assertEqual(summary["control_records"], 1)
            self.assertEqual(summary["sco_headers"][0]["packets"], 1)
            self.assertEqual(summary["files"]["control.jsonl"],
                             {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            self.assertTrue(any("t2026-CentauriBeta.plist" in limitation for limitation in summary["limitations"]))
            self.assertTrue(any("no SCO footer/audio capture" in limitation for limitation in summary["limitations"]))
            monitor_socket.send.assert_not_called()

    def test_capture_error_and_record_limit_remain_incomplete(self):
        for result_item, limit, expected in (([(b"short", [], 0, None)], 10, "capture_error"),
                                             ([(command(0x1005), [], 0, None)], 1, "control_record_limit"),
                                             ([(command(0x1005), [], socket.MSG_TRUNC, None)], 10, "capture_error")):
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "private"
                monitor_socket = MagicMock()
                monitor_socket.recvmsg.side_effect = result_item
                with patch("builtins.print"), patch.object(sco, "MAX_CONTROL_RECORDS", limit):
                    self.assertEqual(sco.capture(monitor_socket, destination, 0, 1, {}), 1)
                summary = json.loads((destination / "summary.json").read_text())
                self.assertEqual(summary["stop_reason"], expected)

    def test_existing_private_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sco.private_write(directory, "evidence.json", "first")
            with self.assertRaises(FileExistsError):
                sco.private_write(directory, "evidence.json", "second")
            self.assertEqual((directory / "evidence.json").read_text(), "first")

    def test_repository_output_is_refused_before_any_socket(self):
        with patch.object(sco.sys, "argv", ["capture", "--output", str(ROOT / "work/private-sco")]), \
             patch.object(sco, "open_monitor") as opening, patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                sco.main()
        self.assertEqual(error.exception.code, 2)
        opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
