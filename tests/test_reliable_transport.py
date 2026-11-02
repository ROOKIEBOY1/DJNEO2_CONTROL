import unittest

from outer import parse_outer
from reliable_transport import ReliableTransport, header


def status_packet(session, ack):
    packet = header(session, 1, 34) + bytes(18)
    packet[24:28] = ack.to_bytes(2, 'little') * 2
    return bytes(packet)


class ReliableTransportTests(unittest.TestCase):
    def test_data_sequence_and_exact_retransmission(self):
        link = ReliableTransport(0x1234, 0xfff8)
        raw = link.encode(b'\x55hello', 0)
        self.assertEqual(parse_outer(raw).channel, 5)
        self.assertEqual(raw[4:6], b'\x00\x00')
        self.assertEqual(raw[8:12], bytes.fromhex('f8ff0000'))
        self.assertEqual(raw[16:20], b'\x01\x01\x00\x00')
        self.assertEqual(link.retries(0.2), [raw])
        link.observe(status_packet(0x1234, 0))
        self.assertEqual(link.acknowledged, 1)
        self.assertEqual(link.retries(0.4), [])

    def test_cumulative_ack_rejects_stale_and_future_values(self):
        link = ReliableTransport(0x1234, 0x4000)
        for i in range(3):
            link.encode(b'x', i / 100)
        link.observe(status_packet(0x1234, 0x4010))
        self.assertEqual(link.acknowledged, 2)
        link.observe(status_packet(0x1234, 0x4008))
        link.observe(status_packet(0x1234, 0x5000))
        link.observe(status_packet(0x1235, 0x4018))
        self.assertEqual(len(link.pending), 1)
        self.assertEqual(link.peer_ack, 0x4010)

    def test_timeout_stops_unacknowledged_data(self):
        link = ReliableTransport(0x1234, 0x4000)
        link.encode(b'x', 0)
        with self.assertRaises(TimeoutError):
            link.retries(1)

    def test_ack_windows_are_independent_and_downlink_is_contiguous(self):
        link = ReliableTransport(0x29ec, 0x4f58)
        link.observe(bytes(header(0x29ec, 2, 16, 0x6000)))
        link.observe(bytes(header(0x29ec, 3, 16, 0x4f68)))
        self.assertEqual(link.downlink_cursor, 0x4f58)
        link.observe(bytes(header(0x29ec, 3, 16, 0x4f60)))
        packet = link.acknowledgement()
        self.assertEqual(len(packet), 34)
        self.assertEqual(packet[8:12], bytes.fromhex('00600060'))
        self.assertEqual(packet[16:20], bytes.fromhex('684f684f'))
        self.assertEqual(packet[24:28], bytes.fromhex('584f584f'))


