import json
from pathlib import Path
import unittest

from handshake import build_handshake
from duml import encode_frame
from fly_command import build_fly_tunnel
from control_packets import build_fly_packet
from neutral_stick import build_stick_payload
from circle_protocol import (build_radius_frame, build_circle_start_frame,
                             build_circle_stop_frame, build_circle_selection_frame)


class GoldenVectorTests(unittest.TestCase):
    def test_language_port_vectors_match_reference(self):
        functions = {
            'handshake.build_handshake': build_handshake,
            'duml.encode_frame': encode_frame,
            'fly_command.build_fly_tunnel': build_fly_tunnel,
            'control_packets.build_fly_packet': build_fly_packet,
            'neutral_stick.build_stick_payload': build_stick_payload,
            'circle_protocol.build_radius_frame': build_radius_frame,
            'circle_protocol.build_circle_start_frame': build_circle_start_frame,
            'circle_protocol.build_circle_stop_frame': build_circle_stop_frame,
            'circle_protocol.build_circle_selection_frame': build_circle_selection_frame,
        }
        path = Path(__file__).resolve().parents[1] / 'samples/golden-vectors.json'
        vectors = json.loads(path.read_text(encoding='utf-8'))['vectors']
        self.assertEqual(len(vectors), 19)
        for vector in vectors:
            with self.subTest(name=vector['name']):
                args = [bytes.fromhex(a['hex']) if isinstance(a, dict) else a for a in vector['args']]
                result = functions[vector['function']](*args)
                self.assertEqual(result.hex(), vector['expected_hex'])
                self.assertEqual(len(result), vector['expected_bytes'])


if __name__ == '__main__':
    unittest.main()
