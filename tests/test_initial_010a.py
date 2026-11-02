import unittest

from initial_010a import build_initial_010a_packet


class Initial010ATests(unittest.TestCase):
    def test_matches_captured_single_frame_packet(self):
        expected = bytes.fromhex(
            '41808c4a000004036054605400000000605460540000000060546054000000001f00'
            '551f044e02a9b5bb00010a010d000004200001084000020000065501049208'
        )
        self.assertEqual(
            build_initial_010a_packet(0x4a8c, 0x5460, 0x5460, 0xbbb5),
            expected,
        )

    def test_rejects_invalid_session(self):
        with self.assertRaises(ValueError):
            build_initial_010a_packet(0, 0x5460, 0x5460, 1)


