import socket
import threading
import unittest

from ground_short_comparison import active_packet, collect_trial
from handshake_probe import build_handshake, build_idle_packet
from initial_010a import build_initial_010a_packet


class GroundShortComparisonTests(unittest.TestCase):
    def test_active_packets_are_bounded_to_empty_or_captured_short_form(self):
        self.assertEqual(active_packet('baseline', 0x4a8c, 0x5460, 0x1234),
                         build_idle_packet(0x4a8c, 0x5460))
        self.assertEqual(active_packet('short', 0x4a8c, 0x5460, 0x1234),
                         build_initial_010a_packet(0x4a8c, 0x5460, 0x5460, 0x1234))
        with self.assertRaises(ValueError):
            active_packet('full', 0x4a8c, 0x5460, 0x1234)

    def test_loopback_trial_sends_only_short_form_during_active_phase(self):
        seen = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(0.5)

            def answer():
                try:
                    while True:
                        packet, peer = server.recvfrom(2048)
                        seen.append(packet)
                        if packet == build_handshake(0x4a8c, 0x5460):
                            server.sendto(bytes.fromhex('0f808c4a00000049010f000505401f'), peer)
                except socket.timeout:
                    pass

            worker = threading.Thread(target=answer)
            worker.start()
            try:
                result = collect_trial('127.0.0.1', '127.0.0.1', server.getsockname()[1],
                                       0x4a8c, 0x5460, 'short', 0x1234,
                                       attempts=1, retry_interval=0.05,
                                       pre_seconds=0.06, active_seconds=0.13,
                                       post_seconds=0.01, cadence=0.06)
            finally:
                worker.join(timeout=1)
        self.assertTrue(result['matched'])
        self.assertGreaterEqual(result['short_packets_sent'], 2)
        self.assertEqual(seen[0], build_handshake(0x4a8c, 0x5460))
        self.assertTrue(all(len(packet) in (34, 65) for packet in seen[1:]))
        self.assertEqual(sum(len(packet) == 65 for packet in seen[1:]),
                         result['short_packets_sent'])

    def test_rejects_active_phase_longer_than_two_seconds(self):
        with self.assertRaises(ValueError):
            collect_trial('127.0.0.1', '127.0.0.1', 9003, 0x4a8c, 0x5460,
                          'short', 0x1234, active_seconds=2.01)


if __name__ == '__main__':
    unittest.main()
