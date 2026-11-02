import unittest

from fly_command import build_fly_tunnel


class FlyCommandTests(unittest.TestCase):
    def test_takeoff_tunnel_matches_real_capture(self):
        expected = bytes.fromhex(
            '553104533be94956005101550e046602037f9740032a01dd32'
            '8e338dbb0249560000ffffffff018200000000000000aeb9'
        )
        self.assertEqual(build_fly_tunnel(1, 0x977f, 0x5649,
                                          bytes.fromhex('8e338dbb02')), expected)

    def test_rejects_missing_dynamic_trailer_prefix(self):
        with self.assertRaises(ValueError):
            build_fly_tunnel(1, 1, 2, b'')

    def test_labeled_landing_tunnel_matches_real_capture(self):
        expected = bytes.fromhex(
            '553104533be95acd005101550e04660203a3c740032a021301'
            '8e338dbb025acd0000ffffffff018200000000000000ee65'
        )
        self.assertEqual(build_fly_tunnel(2, 0xc7a3, 0xcd5a,
                                          bytes.fromhex('8e338dbb02')), expected)


