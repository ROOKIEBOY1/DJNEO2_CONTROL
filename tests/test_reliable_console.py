import socket
import threading
import time
import unittest
import tempfile
from pathlib import Path

from control_packets import valid_frames
from flight_controller import FlightController
from handshake import build_handshake
from duml import encode_frame
from reliable_transport import header
from registration_exchange import PROFILE
from messages import duml_messages, udp_records
from routing_sequences import RoutingSequenceStore


class ReliableConsoleTests(unittest.TestCase):
    def server(self, acknowledge, drop_first=False, peer_request=None,
               reply_after=1, send_video=False, route_highwater=None):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        server.settimeout(0.05)
        stop = threading.Event()
        captured = []
        accepted_cursor = 0x5ef8
        received_sequences = set()
        dropped = False
        query_count = 0
        last_route = route_highwater

        def worker():
            nonlocal accepted_cursor, dropped, query_count, last_route
            while not stop.is_set():
                try:
                    raw, peer = server.recvfrom(4096)
                except socket.timeout:
                    continue
                captured.append(raw)
                if raw == build_handshake(0xebdf, 0x5ef8):
                    accepted_cursor = 0x5ef8
                    received_sequences.clear()
                    server.sendto(bytes.fromhex('0f80dfeb000000bb010f000505401f'), peer)
                    if peer_request is not None:
                        tunnel = encode_frame(0x27, 0xee, 9, 0, 0x51, 1, peer_request)
                        packet = header(0xebdf, 1, 34 + len(tunnel)) + bytes(16)
                        packet += len(tunnel).to_bytes(2, 'little') + tunnel
                        server.sendto(bytes(packet), peer)
                elif len(raw) >= 20 and raw[6] == 5 and acknowledge:
                    if drop_first and not dropped:
                        dropped = True
                        continue
                    received_sequences.add(int.from_bytes(raw[4:6], 'little'))
                    while ((accepted_cursor + 8) & 0xffff) in received_sequences:
                        accepted_cursor = (accepted_cursor + 8) & 0xffff
                        received_sequences.remove(accepted_cursor)
                    ack = header(0xebdf, 1, 34) + bytes(18)
                    ack[24:28] = accepted_cursor.to_bytes(2, 'little') * 2
                    server.sendto(bytes(ack), peer)
                    for frame in valid_frames(raw[20:]):
                        if (last_route is not None and frame[4:6] == b'\x3b\xe9'
                                and frame[9:11] == b'\x51\x01'):
                            route = int.from_bytes(frame[-19:-15], 'little')
                            if route <= last_route:
                                continue
                            last_route = route
                        for inner in valid_frames(frame[11:-2]):
                            if inner[9:11] == b'\x03\x34':
                                query_count += 1
                                if query_count < reply_after:
                                    continue
                                reply = encode_frame(3, 2, int.from_bytes(inner[6:8], 'little'),
                                                     0x80, 3, 0x34, b'\x00DJI Neo 2')
                                tunnel = encode_frame(0x27, 0xee, 1, 0, 0x51, 1, reply)
                                packet = header(0xebdf, 1, 34 + len(tunnel)) + bytes(16)
                                packet += len(tunnel).to_bytes(2, 'little') + tunnel
                                packet[24:28] = raw[4:6] * 2
                                server.sendto(bytes(packet), peer)
                                if send_video:
                                    server.sendto(bytes(header(0xebdf, 2, 16, 0x8080)), peer)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return server, stop, thread, captured

    def test_query_uses_reliable_data_and_peer_ack(self):
        server, stop, thread, captured = self.server(True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.1,
                                                  reliable=True)
            try:
                self.assertIn(b'DJI Neo 2', controller.query_device(timeout=0.5))
                self.assertTrue(controller.device_verified)
                self.assertGreater(controller.transport.acknowledged, 0)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()
        self.assertTrue(any(raw[6] == 5 for raw in captured))
        self.assertFalse(any(raw[6] == 4 and len(raw) > 34 for raw in captured))

    def test_missing_transport_ack_stops_sender(self):
        server, stop, thread, captured = self.server(False)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0,
                                                  reliable=True)
            try:
                deadline = time.monotonic() + 1.5
                while not controller._stop.is_set() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(controller._stop.is_set())
                count = len(captured)
                time.sleep(0.12)
                self.assertEqual(len(captured), count)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    def test_recovers_from_first_datagram_loss(self):
        server, stop, thread, captured = self.server(True, drop_first=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.4,
                                                  reliable=True)
            try:
                self.assertGreater(controller.transport.acknowledged, 0)
                self.assertGreater(controller.transport.retransmitted, 0)
                self.assertIsNone(controller.last_error)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_registration_answers_current_peer_sequence_over_reliable_data(self):
        path = PROFILE.parent.parent / 'captures' / 'cold-all-retry-20261027.pcapng'
        request = None
        for _, direction, raw in udp_records(path):
            if direction != 'plane' or int.from_bytes(raw[2:4], 'little') != 0x4a8c:
                continue
            for frame, _ in duml_messages(raw):
                if frame[4:6] == b'\xe9\xee' and frame[9:11] == b'\x51\x41' and len(frame) == 26:
                    request = encode_frame(0xe9, 0xee, 0x4321, 0x40, 0x51, 0x41, frame[11:-2])
                    break
            if request is not None:
                break
        self.assertIsNotNone(request)
        server, stop, thread, captured = self.server(True, peer_request=request)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.3,
                                                  reliable=True, registration_profile=PROFILE)
            try:
                self.assertGreater(controller.registration.responses, 0)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()
        responses = []
        for raw in captured:
            if raw[6] != 5:
                continue
            for frame, _ in duml_messages(raw):
                if frame[9:11] == b'\x51\x41' and frame[8] == 0xc0:
                    responses.append(frame)
        self.assertTrue(responses)
        self.assertEqual(responses[0][6:8], b'\x21\x43')

    def test_initialization_waits_for_delayed_device_and_video(self):
        server, stop, thread, captured = self.server(True, reply_after=2, send_video=True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.1,
                                                  reliable=True)
            try:
                self.assertTrue(controller.initialize_control_link(timeout=3))
                self.assertIn(b'DJI Neo 2', controller.device_reply)
                self.assertIsNotNone(controller.last_video_at)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    def test_initialization_without_video_is_not_ready(self):
        server, stop, thread, captured = self.server(True)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.1,
                                                  reliable=True)
            try:
                self.assertFalse(controller.initialize_control_link(timeout=1))
                self.assertTrue(controller.device_verified)
                self.assertFalse(controller.takeoff_sent)
            finally:
                controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()

    def test_reconnect_with_persistent_counter_avoids_simulated_stale_route_rejection(self):
        server, stop, thread, captured = self.server(True, route_highwater=0xf000)
        try:
            controller = FlightController.connect('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                                  0xebdf, 0x5ef8, attempts=1,
                                                  retry_interval=0.1, prepare_seconds=0.05,
                                                  reliable=True, outer_sequence=0x100)
            try:
                self.assertIsNone(controller.query_device(timeout=0.05))
                self.assertGreater(controller.transport.acknowledged, 0)
            finally:
                controller.close()
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / 'routing.json'
                for _ in range(2):
                    with RoutingSequenceStore(state, initial=0x10000, block_size=4) as routing:
                        controller = FlightController.connect(
                            '127.0.0.1', '127.0.0.1', server.getsockname()[1],
                            0xebdf, 0x5ef8, attempts=1, retry_interval=0.1,
                            prepare_seconds=0.05, reliable=True, outer_sequence=0x100,
                            routing_sequences=routing)
                        try:
                            self.assertIn(b'DJI Neo 2', controller.query_device(timeout=0.3))
                            self.assertFalse(controller.takeoff_sent)
                        finally:
                            controller.close()
        finally:
            stop.set()
            thread.join(1)
            server.close()


if __name__ == '__main__':
    unittest.main()
