import unittest
from duml import encode_frame, build_takeoff

class CaptureTests(unittest.TestCase):
    def test_inner_frame_matches_capture(self):
        self.assertEqual(build_takeoff(0x977f), bytes.fromhex('550e046602037f9740032a01dd32'))

    def test_tunnel_matches_capture(self):
        inner = build_takeoff(0x977f)
        trailer = bytes.fromhex('8e338dbb0249560000ffffffff018200000000000000')
        tunnel = encode_frame(0x3b, 0xe9, 0x5649, 0, 0x51, 1, inner + trailer)
        self.assertEqual(tunnel, bytes.fromhex('553104533be94956005101550e046602037f9740032a01dd328e338dbb0249560000ffffffff018200000000000000aeb9'))


