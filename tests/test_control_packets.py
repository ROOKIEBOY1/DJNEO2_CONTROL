import unittest
import json
from pathlib import Path

from control_packets import (build_fly_packet, build_query_packet,
                             build_startup_query_packet,
                             find_command_reply, find_fly_reply,
                             find_query_reply)
from duml import encode_frame


PLANE_TUNNEL = bytes.fromhex(
    '5531045327eee8d2005101550e04660302a6c180032a00ad28'
    'a9f5542902adc40300ffffffff0182000000000000003648'
)


def plane_reply(session=0xebdf):
    body = bytes.fromhex('f85ef85e00000000f85ef85e00000000') + len(PLANE_TUNNEL).to_bytes(2, 'little') + PLANE_TUNNEL
    header = bytearray(16)
    header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
    header[2:4] = session.to_bytes(2, 'little')
    header[6] = 1
    header[7] = 0
    for value in header[:7]:
        header[7] ^= value
    return bytes(header) + body


class ControlPacketTests(unittest.TestCase):
    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_actual_successful_takeoff_and_landing_replies_are_recognized(self):
        from messages import duml_messages

        path = Path(__file__).resolve().parents[1] / 'captures' / 'control-attempt-20261029-112328.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        session = int(record['status']['session'], 0)
        commands = {c['inner_sequence']: c['param'] for c in record['commands'] if 'param' in c}
        found = set()
        for row in record['incoming_control']:
            raw = bytes.fromhex(row['hex'])
            for frame, wrapper in duml_messages(raw):
                sequence = int.from_bytes(frame[6:8], 'little')
                if frame[4:6] == b'\x03\x02' and frame[9:11] == b'\x03\x2a' and sequence in commands:
                    envelope = 'direct' if wrapper is None else wrapper[4:6].hex()
                    self.assertEqual(find_fly_reply(raw, session, sequence), b'\x00',
                                     f'param={commands[sequence]} envelope={envelope}')
                    self.assertIsNone(find_fly_reply(raw, session ^ 1, sequence))
                    self.assertIsNone(find_fly_reply(raw, session, (sequence + 1) & 0xffff))
                    found.add(commands[sequence])
        self.assertEqual(found, {1, 2})

    def test_takeoff_packet_wraps_real_tunnel(self):
        packet = build_fly_packet(0xebdf, 0x5ef8, 0xb5a0, 0x5ef8,
                                  0xc87f, 0xc1a6, 1)
        self.assertEqual(len(packet), 83)
        self.assertEqual(packet[:8].hex(), '5380dfeb000004e3')
        self.assertEqual(packet[34:].hex(),
                         '553104533be97fc8005101550e04660203a6c140032a01971c'
                         '8e338dbb027fc80000ffffffff018200000000000000d502')

    def test_matching_flight_reply_uses_session_and_inner_sequence(self):
        packet = plane_reply()
        self.assertEqual(find_fly_reply(packet, 0xebdf, 0xc1a6), b'\x00')
        self.assertIsNone(find_fly_reply(packet, 0xebde, 0xc1a6))
        self.assertIsNone(find_fly_reply(packet, 0xebdf, 0xc1a7))

    def test_device_query_matches_captured_tunnel(self):
        packet = build_query_packet(0xebdf, 0x5ef8, 0x79f0, 0x5ef8,
                                    0xc1d4, 0xb962)
        self.assertEqual(packet[34:].hex(),
                         '553004973be9d4c1005101550d0433020362b9400334598b'
                         '8e338dbb02d4c10000ffffffff018200000000000000c69a')

    def test_four_startup_queries_match_captured_frames(self):
        packet = build_startup_query_packet(0x29ec, 0x4f58, 0x4f58,
                                            0x4f58, 0x83d3)
        self.assertEqual(len(packet), 86)
        self.assertEqual(packet[34:].hex(),
                         '550d0433020ed3834000015c37'
                         '550d0433021fd4834000011b41'
                         '550d043302a2d583400001f2bd'
                         '550d04330200d683400001aa28')

    def test_device_query_reply_matches_inner_sequence(self):
        inner = encode_frame(3, 2, 0xb962, 0x80, 3, 0x34, b'\x00DJI Neo 2')
        tunnel = encode_frame(0x27, 0xee, 0xacd2, 0, 0x51, 1, inner)
        body = bytes.fromhex('f85ef85e00000000f85ef85e00000000')
        body += len(tunnel).to_bytes(2, 'little') + tunnel
        header = bytearray(16)
        header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
        header[2:4] = (0xebdf).to_bytes(2, 'little')
        header[6] = 1
        for value in header[:7]:
            header[7] ^= value
        packet = bytes(header) + body
        self.assertIn(b'DJI Neo 2', find_query_reply(packet, 0xebdf, 0xb962))
        self.assertIsNone(find_query_reply(packet, 0xebdf, 0xb963))

    def test_startup_query_reply_matches_target_and_sequence(self):
        inner = encode_frame(0x1f, 2, 0x83d4, 0xc0, 0, 1, b'\x00WA020 AC Ver.A')
        tunnel = encode_frame(0x27, 0xee, 0x0a63, 0, 0x51, 1, inner)
        body = bytes.fromhex('584f584f00000000584f584f00000000')
        body += len(tunnel).to_bytes(2, 'little') + tunnel
        header = bytearray(16)
        header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
        header[2:4] = (0x29ec).to_bytes(2, 'little')
        header[6] = 1
        for value in header[:7]:
            header[7] ^= value
        packet = bytes(header) + body
        self.assertIsNotNone(find_command_reply(packet, 0x29ec, 0x83d4,
                                                 0, 1, sender=0x1f))
        self.assertIsNone(find_command_reply(packet, 0x29ec, 0x83d5,
                                              0, 1, sender=0x1f))


if __name__ == '__main__':
    unittest.main()
