import socket
import threading
import time
import unittest
from unittest.mock import patch

from control_packets import valid_frames
from flight_controller import FlightController
from handshake import build_handshake
from duml import encode_frame


SHORT_REPLY = bytes.fromhex('0f80dfeb000000bb010f000505401f')


def reply_for(sequence):
    inner = encode_frame(3, 2, sequence, 0x80, 3, 0x2a, b'\x00')
    trailer = bytes.fromhex('a9f5542902adc40300ffffffff018200000000000000')
    tunnel = encode_frame(0x27, 0xee, 0xd2e8, 0, 0x51, 1, inner + trailer)
    body = bytes.fromhex('f85ef85e00000000f85ef85e00000000') + len(tunnel).to_bytes(2, 'little') + tunnel
    header = bytearray(16)
    header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
    header[2:4] = (0xebdf).to_bytes(2, 'little')
    header[6] = 1
    for byte in header[:7]:
        header[7] ^= byte
    return bytes(header) + body


class FlightConsoleTests(unittest.TestCase):
    def test_video_receiver_is_attached_before_connection_reader_and_errors_are_isolated(self):
        class Sink:
            def __init__(self):
                self.seen = threading.Event()
            def submit(self, raw):
                self.seen.set()
                raise RuntimeError('decoder unavailable')
            def status(self):
                return {'test': True}
            def set_subject_provider(self,provider):
                self.subject_provider=provider
        sink = Sink()
        server, captured, stop, thread = self.run_server(False, send_video=True)
        controller = None
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                0xebdf, 0x5ef8, attempts=1, retry_interval=.1, prepare_seconds=0,
                tick_interval=.03, video_receiver=sink)
            self.assertTrue(sink.seen.wait(.5))
            deadline = time.monotonic() + .5
            while controller.video_receiver_error is None and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(controller.video_receiver_error, 'decoder unavailable')
            self.assertFalse(controller._stop.is_set())
            self.assertTrue(controller._reader.is_alive())
            self.assertFalse(sink.subject_provider()['lock_verified'])
        finally:
            if controller:
                controller.close()
            stop.set()
            thread.join(.5)
            server.close()

    def run_server(self, respond_to_takeoff, send_video=False,
                   respond_to_query=False, respond_to_land=False):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(0.1)
        captured = []
        stop = threading.Event()

        def worker():
            while not stop.is_set():
                try:
                    raw, peer = server.recvfrom(4096)
                except socket.timeout:
                    continue
                captured.append(raw)
                if raw == build_handshake(0xebdf, 0x5ef8):
                    server.sendto(SHORT_REPLY, peer)
                    if send_video:
                        sequences = (0x8080,) if send_video is True else send_video
                        for sequence in sequences:
                            header = bytearray(16)
                            header[:2] = (0x8010).to_bytes(2, 'little')
                            header[2:4] = (0xebdf).to_bytes(2, 'little')
                            header[4:6] = sequence.to_bytes(2, 'little')
                            header[6] = 2
                            for value in header[:7]:
                                header[7] ^= value
                            server.sendto(bytes(header), peer)
                elif len(raw) >= 34 and raw[6] == 4:
                    for outer in valid_frames(raw[34:]):
                        for inner in valid_frames(outer[11:-2]):
                            if (respond_to_takeoff and inner[9:11] == b'\x03\x2a'
                                    and inner[11:-2] == b'\x01'):
                                server.sendto(reply_for(int.from_bytes(inner[6:8], 'little')), peer)
                            if (respond_to_land and inner[9:11] == b'\x03\x2a'
                                    and inner[11:-2] == b'\x02'):
                                server.sendto(reply_for(int.from_bytes(inner[6:8], 'little')), peer)
                            if respond_to_query and inner[9:11] == b'\x03\x34':
                                inner_reply = encode_frame(3, 2, int.from_bytes(inner[6:8], 'little'),
                                                           0x80, 3, 0x34, b'\x00DJI Neo 2')
                                tunnel = encode_frame(0x27, 0xee, 0xd2e8, 0, 0x51, 1,
                                                      inner_reply)
                                body = bytes.fromhex('f85ef85e00000000f85ef85e00000000')
                                body += len(tunnel).to_bytes(2, 'little') + tunnel
                                header = bytearray(16)
                                header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
                                header[2:4] = (0xebdf).to_bytes(2, 'little')
                                header[6] = 1
                                for value in header[:7]:
                                    header[7] ^= value
                                server.sendto(bytes(header) + body, peer)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return server, captured, stop, thread

    def test_takeoff_requires_matching_flight_reply(self):
        server, captured, stop, thread = self.run_server(True, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  inner_sequence=0xc1a6, outer_sequence=0xc87f)
            try:
                deadline = time.monotonic() + 0.5
                while controller.video_ack != 0x8080 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(controller.video_ack, 0x8080)
                self.assertEqual(controller.takeoff(timeout=0.5), b'\x00')
                self.assertTrue(controller.takeoff_acknowledged)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()

    def test_default_takeoff_does_not_arm_timed_landing(self):
        server, captured, stop, thread = self.run_server(True, send_video=True, respond_to_land=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0.05, tick_interval=0.03)
            try:
                with patch('flight_controller.threading.Timer') as timer:
                    self.assertEqual(controller.takeoff(timeout=0.5), b'\x00')
                    timer.assert_not_called()
                self.assertIsNone(controller._watchdog)
                self.assertFalse(controller.auto_land_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    def test_two_explicit_flight_cycles_in_one_connection(self):
        server, captured, stop, thread = self.run_server(True, send_video=True, respond_to_land=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0.05, tick_interval=0.03)
            try:
                self.assertEqual(controller.takeoff(timeout=0.5), b'\x00')
                self.assertEqual(controller.land(timeout=0.5), b'\x00')
                with self.assertRaises(RuntimeError):
                    controller.move('left', 0.05)
                with self.assertRaises(ValueError):
                    controller.takeoff_again(confirmed_grounded=False)
                from test_landing_status import osd
                with controller._lock:
                    controller.landing_status.observe(osd(flags=0))
                self.assertEqual(controller.takeoff_again(confirmed_grounded=True, timeout=0.5), b'\x00')
                self.assertFalse(controller.land_sent)
                self.assertFalse(controller.land_requested)
                self.assertEqual(controller.flight_cycle, 2)
                controller._auto_land(expected_cycle=1)
                self.assertFalse(controller.land_sent)
                self.assertFalse(controller.auto_land_sent)
                controller.move('left', 0.05)
                self.assertEqual(controller.land(timeout=0.5), b'\x00')
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()
        params = [f[11:-2] for raw in captured if len(raw) >= 34
                  for outer in valid_frames(raw[34:]) for f in valid_frames(outer[11:-2])
                  if f[9:11] == b'\x03\x2a']
        self.assertEqual(params, [b'\x01', b'\x02', b'\x01', b'\x02'])

    def test_takeoff_again_without_landing_ack_is_rejected(self):
        server, captured, stop, thread = self.run_server(True, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0.05, tick_interval=0.03)
            try:
                controller.takeoff(timeout=0.5)
                controller.land(timeout=0.05)
                with self.assertRaises(RuntimeError):
                    controller.takeoff_again(confirmed_grounded=True)
                self.assertTrue(controller.land_sent)
                self.assertTrue(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    def test_no_takeoff_reply_blocks_forward(self):
        server, captured, stop, thread = self.run_server(False, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  inner_sequence=0xc1a6, outer_sequence=0xc87f)
            try:
                self.assertIsNone(controller.takeoff(timeout=0.12))
                with self.assertRaises(RuntimeError):
                    controller.forward(0.1)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        flight_params = []
        for raw in captured:
            if len(raw) < 34 or raw[6] != 4:
                continue
            for outer in valid_frames(raw[34:]):
                flight_params.extend(inner[11:-2] for inner in valid_frames(outer[11:-2])
                                     if inner[9:11] == b'\x03\x2a')
        self.assertEqual(flight_params, [b'\x01', b'\x02'])

    def test_device_query_receives_reply_without_enabling_flight(self):
        server, captured, stop, thread = self.run_server(False, respond_to_query=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                self.assertIn(b'DJI Neo 2', controller.query_device(timeout=0.5))
                self.assertFalse(controller.takeoff_sent)
                self.assertFalse(controller.takeoff_acknowledged)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()

    def test_startup_probe_sends_only_four_zero_payload_queries(self):
        server, captured, stop, thread = self.run_server(False)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                self.assertIsNone(controller.probe_startup_queries(timeout=0.1))
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        bundles = [raw for raw in captured if len(raw) == 86 and raw[6] == 4]
        self.assertEqual(len(bundles), 1)
        frames = list(valid_frames(bundles[0][34:]))
        self.assertEqual([(f[5], f[9:11], f[11:-2]) for f in frames],
                         [(target, b'\x00\x01', b'')
                          for target in (0x0e, 0x1f, 0xa2, 0x00)])

    def test_forward_auto_returns_to_neutral(self):
        server, captured, stop, thread = self.run_server(False)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  inner_sequence=0xc1a6, outer_sequence=0xc87f)
            try:
                controller.takeoff_acknowledged = True
                controller.forward(0.12)
                time.sleep(0.09)
                controller.land(timeout=0.01)
                with self.assertRaises(RuntimeError):
                    controller.forward(0.1)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        pitches = []
        for raw in captured:
            if len(raw) < 75 or raw[6] != 4:
                continue
            for frame in valid_frames(raw[34:]):
                if frame[4:6] == b'\x02\xa9' and frame[9:11] == b'\x01\x0a' and len(frame) == 41:
                    bits = int.from_bytes(frame[11:-2], 'little')
                    pitches.append((bits >> 35) & 2047)
        self.assertIn(1359, pitches)
        self.assertIn(1024, pitches[pitches.index(1359) + 1:])

    def test_land_sends_captured_parameter_even_without_reply(self):
        server, captured, stop, thread = self.run_server(False)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  inner_sequence=0xc1a6, outer_sequence=0xc87f)
            try:
                self.assertIsNone(controller.land(timeout=0.12))
                self.assertTrue(controller.land_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        landing = []
        for raw in captured:
            if len(raw) < 34 or raw[6] != 4:
                continue
            for outer in valid_frames(raw[34:]):
                landing.extend(inner[11:-2] for inner in valid_frames(outer[11:-2])
                               if inner[9:11] == b'\x03\x2a')
        self.assertEqual(landing, [b'\x02'])

    def test_watchdog_sends_landing_after_acknowledged_takeoff(self):
        server, captured, stop, thread = self.run_server(True, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  inner_sequence=0xc1a6, outer_sequence=0xc87f,
                                                  max_flight_seconds=0.15)
            try:
                self.assertEqual(controller.takeoff(timeout=0.5), b'\x00')
                time.sleep(0.25)
                self.assertTrue(controller.auto_land_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        landing = []
        for raw in captured:
            if len(raw) < 34 or raw[6] != 4:
                continue
            for outer in valid_frames(raw[34:]):
                landing.extend(inner[11:-2] for inner in valid_frames(outer[11:-2])
                               if inner[9:11] == b'\x03\x2a' and inner[11:-2] == b'\x02')
        self.assertEqual(landing, [b'\x02'])

    def test_forward_refuses_stale_downlink(self):
        server, captured, stop, thread = self.run_server(False)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                controller.takeoff_acknowledged = True
                controller.last_receive_at = time.monotonic() - 2
                with self.assertRaises(RuntimeError):
                    controller.forward(0.1)
                with self.assertRaises(RuntimeError):
                    controller.takeoff(timeout=0.1)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()

    def test_watchdog_lands_when_takeoff_reply_is_missing(self):
        server, captured, stop, thread = self.run_server(False, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03,
                                                  max_flight_seconds=0.15)
            try:
                self.assertIsNone(controller.takeoff(timeout=0.05))
                time.sleep(0.23)
                self.assertTrue(controller.auto_land_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()

    def test_flight_packet_ack_tracks_latest_video_sequence(self):
        server, captured, stop, thread = self.run_server(False, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                deadline = time.monotonic() + 0.5
                while controller.video_ack != 0x8080 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(controller.video_ack, 0x8080)
                controller.takeoff(timeout=0.05)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()
        takeoffs = [raw for raw in captured if len(raw) == 83 and raw[6] == 4
                    and any(inner[9:11] == b'\x03\x2a' and inner[11:-2] == b'\x01'
                            for outer in valid_frames(raw[34:])
                            for inner in valid_frames(outer[11:-2]))]
        self.assertEqual(len(takeoffs), 1)
        self.assertEqual(takeoffs[0][8:12], bytes.fromhex('80808080'))
        self.assertTrue(any(len(raw) == 75 and raw[6] == 4 and
                            raw[8:12] == bytes.fromhex('80808080')
                            for raw in captured))

    def test_late_older_video_packet_does_not_move_ack_backwards(self):
        server, captured, stop, thread = self.run_server(False, send_video=(0x8080, 0x8078))
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                time.sleep(0.1)
                self.assertEqual(controller.video_ack, 0x8080)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()

    def test_takeoff_refuses_when_video_has_stopped(self):
        server, captured, stop, thread = self.run_server(False, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                                                  prepare_seconds=0, tick_interval=0.03)
            try:
                controller.last_video_at = time.monotonic() - 2
                with self.assertRaises(RuntimeError):
                    controller.takeoff(timeout=0.1)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(timeout=1)
            server.close()


if __name__ == '__main__':
    unittest.main()
