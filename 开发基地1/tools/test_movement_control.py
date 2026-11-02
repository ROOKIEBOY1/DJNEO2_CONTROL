import socket
import threading
import time
import unittest

from capture_messages import duml_messages
from flight_console import FlightController
from handshake_probe import build_handshake
from reliable_transport import header


def stick_axes(raw):
    for frame, _ in duml_messages(raw):
        if frame[4:6] == b'\x02\xa9' and frame[9:11] == b'\x01\x0a' and len(frame) == 41:
            bits = int.from_bytes(frame[11:-2], 'little')
            return tuple((bits >> (24 + 11 * i)) & 2047 for i in range(4))
    return None


class MovementControlTests(unittest.TestCase):
    def setUp(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server.bind(('127.0.0.1', 0))
        self.server.settimeout(0.03)
        self.captured = []
        self.stop = threading.Event()

        def worker():
            while not self.stop.is_set():
                try:
                    raw, peer = self.server.recvfrom(4096)
                except (socket.timeout, ConnectionResetError):
                    # Windows may deliver an ICMP reset after the test client
                    # closes. Keep the local UDP fixture alive for teardown.
                    continue
                self.captured.append(raw)
                if raw == build_handshake(0xebdf, 0x5ef8):
                    self.server.sendto(bytes.fromhex('0f80dfeb000000bb010f000505401f'), peer)
                elif len(raw) >= 20 and raw[6] == 5:
                    packet = header(0xebdf, 1, 34) + bytes(18)
                    packet[24:28] = raw[4:6] * 2
                    self.server.sendto(bytes(packet), peer)

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()
        self.controller = FlightController.connect(
            '127.0.0.1', '127.0.0.1', self.server.getsockname()[1],
            0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
            prepare_seconds=0, tick_interval=0.02, reliable=True)
        self.controller.takeoff_acknowledged = True

    def tearDown(self):
        self.controller.close()
        self.stop.set()
        self.thread.join(1)
        self.server.close()

    def test_each_direction_moves_one_axis_then_sends_all_centers(self):
        expected = {
            'forward': (1024, 1359, 1024, 1024),
            'backward': (1024, 689, 1024, 1024),
            'up': (1024, 1024, 1359, 1024),
            'down': (1024, 1024, 689, 1024),
            'left': (689, 1024, 1024, 1024),
            'right': (1359, 1024, 1024, 1024),
            'turn_left': (1024, 1024, 1024, 689),
            'turn_right': (1024, 1024, 1024, 1359),
        }
        for direction, axes in expected.items():
            with self.subTest(direction=direction):
                start = len(self.captured)
                self.controller.move(direction, 0.06)
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline:
                    observed = [v for raw in self.captured[start:]
                                if (v := stick_axes(raw)) is not None]
                    if axes in observed and observed[-1] == (1024,) * 4:
                        break
                    time.sleep(0.005)
                self.assertIn(axes, observed)
                self.assertEqual(observed[-1], (1024,) * 4)
                self.assertTrue(all(v in (axes, (1024,) * 4) for v in observed))

    def test_landing_cancels_an_active_ascent_and_prevents_more_motion(self):
        errors = []

        def ascend():
            try:
                self.controller.move('up', 10)
            except RuntimeError as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=ascend)
        worker.start()
        deadline = time.monotonic() + 0.3
        while not any(stick_axes(raw) == (1024, 1024, 1359, 1024) for raw in self.captured):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        self.controller.land(timeout=0.05)
        worker.join(0.4)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        land_index = next(i for i, raw in enumerate(self.captured)
                          if any(f[9:11] == b'\x03\x2a' and f[11:-2] == b'\x02'
                                 for f, _ in duml_messages(raw)))
        self.assertTrue(all(v == (1024,) * 4 for raw in self.captured[land_index:]
                            if (v := stick_axes(raw)) is not None))
        with self.assertRaises(RuntimeError):
            self.controller.move('up', 0.1)

    def test_invalid_durations_and_missing_takeoff_are_rejected(self):
        for seconds in (0, -1, 10.01, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.controller.move('down', seconds)
        self.controller.takeoff_acknowledged = False
        with self.assertRaises(RuntimeError):
            self.controller.move('left', 0.1)

    def test_incoming_subject_frame_appears_in_read_only_status(self):
        from offline_takeoff import encode_frame
        payload = bytes.fromhex('0224f20a00000b00000104010b0002017515ca123f078f11')
        frame = encode_frame(0x92, 2, 10, 0, 10, 0xbc, payload)
        raw = header(0xebdf, 1, 34+len(frame)) + bytes(18) + frame
        self.server.sendto(bytes(raw), self.controller.sock.getsockname())
        deadline = time.monotonic()+.5
        while time.monotonic() < deadline:
            observed = self.controller.status()['subject_observation']
            if observed['candidate_count'] == 1:
                break
            time.sleep(.005)
        self.assertEqual(observed['candidate_count'], 1)
        self.assertEqual(observed['candidates'][0]['id'], 11)
        self.assertFalse(observed['lock_verified'])
        self.assertEqual(self.controller.movements, [])


if __name__ == '__main__':
    unittest.main()
