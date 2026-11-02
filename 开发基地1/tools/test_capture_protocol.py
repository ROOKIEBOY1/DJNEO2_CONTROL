import unittest

from capture_protocol import parse_outer, decode_handshake, summarize_exchange


class CaptureProtocolTests(unittest.TestCase):
    def test_short_reply_has_eight_byte_header(self):
        packet = bytes.fromhex('0f808c4a00000049010f000505401f')
        outer = parse_outer(packet)
        self.assertEqual((outer.session, outer.channel, outer.header_length, outer.body.hex()),
                         (0x4a8c, 0, 8, '010f000505401f'))

    def test_cold_handshake_has_sixteen_byte_header(self):
        packet = bytes.fromhex('30808c4a00000076605464006400c005140000640000019001c005140000640014006400c00514000064000101040102')
        outer = parse_outer(packet)
        self.assertEqual((outer.session, outer.channel, outer.header_length), (0x4a8c, 0, 16))
        self.assertEqual(decode_handshake(outer), '140000640000019001c005140000640014006400c00514000064000101040102')

    def test_bad_xor_is_rejected(self):
        packet = bytearray.fromhex('0f808c4a00000049010f000505401f')
        packet[7] ^= 1
        with self.assertRaises(ValueError):
            parse_outer(bytes(packet))

    def test_exchange_timeline_from_observed_packets(self):
        outbound = bytes.fromhex('30808c4a00000076605464006400c005140000640000019001c005140000640014006400c00514000064000101040102')
        inbound = bytes.fromhex('0f808c4a00000049010f000505401f')
        summary = summarize_exchange([(0.0, 'to', outbound), (0.0593, 'from', inbound)])
        self.assertEqual(summary['session'], 0x4a8c)
        self.assertEqual(summary['short_reply_delay_ms'], 59.3)


if __name__ == '__main__':
    unittest.main()
