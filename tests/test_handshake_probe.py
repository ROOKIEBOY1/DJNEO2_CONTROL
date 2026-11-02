import socket
import unittest

from handshake import build_handshake, build_idle_packet, probe, probe_repeated, new_video_seed


class HandshakeProbeTests(unittest.TestCase):
    def test_build_matches_cold_capture(self):
        self.assertEqual(
            build_handshake(0x4a8c, 0x5460).hex(),
            '30808c4a00000076605464006400c005140000640000019001c005140000640014006400c00514000064000101040102',
        )

    def test_idle_packet_matches_phone_capture(self):
        self.assertEqual(build_idle_packet(0x4a8c, 0x5460).hex(),
                         '22808c4a000004606054605400000000605460540000000060546054000000000000')

    def test_loopback_recognizes_matching_short_reply(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
            def answer():
                request, peer = server.recvfrom(2048)
                self.assertEqual(request, build_handshake(0x4a8c, 0x5460))
                server.sendto(bytes.fromhex('0f808c4a00000049010f000505401f'), peer)
            import threading
            worker = threading.Thread(target=answer)
            worker.start()
            try:
                result = probe('127.0.0.1', server.getsockname()[1], '127.0.0.1', 0x4a8c, 0x5460, 1)
            finally:
                worker.join(timeout=2)
            self.assertTrue(result['matched'])
            self.assertEqual(result['reply_hex'], '0f808c4a00000049010f000505401f')

    def test_rejects_non_drone_local_ip(self):
        from handshake import validate_drone_local_ip
        with self.assertRaises(ValueError):
            validate_drone_local_ip('192.168.1.7')

    def test_bounded_retries_find_later_reply(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
            observed = []
            def answer():
                for _ in range(3):
                    request, peer = server.recvfrom(2048)
                    observed.append(request)
                server.sendto(bytes.fromhex('0f808c4a00000049010f000505401f'), peer)
            import threading
            worker = threading.Thread(target=answer)
            worker.start()
            try:
                result = probe_repeated('127.0.0.1', server.getsockname()[1], '127.0.0.1',
                                        0x4a8c, 0x5460, attempts=5, interval=0.05)
            finally:
                worker.join(timeout=2)
            self.assertEqual(result['attempts_sent'], 3)
            self.assertTrue(result['matched'])
            self.assertEqual(observed, [build_handshake(0x4a8c, 0x5460)] * 3)

    def test_sixty_attempt_window_is_allowed_but_more_is_rejected(self):
        from handshake import validate_retry_bounds
        self.assertEqual(validate_retry_bounds(60, 0.55), (60, 0.55))
        with self.assertRaises(ValueError):
            validate_retry_bounds(61, 0.55)

    def test_new_video_seed_is_nonzero_and_eight_aligned(self):
        for _ in range(100):
            seed = new_video_seed()
            self.assertTrue(0 < seed <= 0xfff8)
            self.assertEqual(seed % 8, 0)

    def test_collects_followup_packet_after_handshake_reply(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
            followup = bytes.fromhex('22808c4a000001656054605400000000605460540000000060546054000000000000')
            def answer():
                _, peer = server.recvfrom(2048)
                server.sendto(bytes.fromhex('0f808c4a00000049010f000505401f'), peer)
                server.sendto(followup, peer)
            import threading
            worker = threading.Thread(target=answer)
            worker.start()
            try:
                result = probe_repeated('127.0.0.1', server.getsockname()[1], '127.0.0.1',
                                        0x4a8c, 0x5460, attempts=2, interval=0.05,
                                        after_match_seconds=0.1)
            finally:
                worker.join(timeout=2)
            self.assertTrue(result['matched'])
            self.assertEqual(result['followup_channels'].get('1'), 1)

    def test_idle_phase_sends_only_captured_empty_packets(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(1)
            received = []
            def answer():
                _, peer = server.recvfrom(2048)
                server.sendto(bytes.fromhex('0f808c4a00000049010f000505401f'), peer)
                try:
                    while True:
                        packet, _ = server.recvfrom(2048)
                        received.append(packet)
                except socket.timeout:
                    pass
            import threading
            worker = threading.Thread(target=answer)
            worker.start()
            try:
                result = probe_repeated('127.0.0.1', server.getsockname()[1], '127.0.0.1',
                                        0x4a8c, 0x5460, attempts=1, interval=0.05,
                                        after_match_seconds=0.24, idle_seconds=0.18,
                                        idle_interval=0.06)
            finally:
                worker.join(timeout=2)
            self.assertGreaterEqual(result['idle_packets_sent'], 2)
            self.assertEqual(received, [build_idle_packet(0x4a8c, 0x5460)] * result['idle_packets_sent'])


