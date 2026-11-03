import unittest

from neutral_stick import build_neutral_stick_packet, build_stick_payload, build_stick_packet


class NeutralStickTests(unittest.TestCase):
    def test_matches_captured_neutral_packet(self):
        expected = bytes.fromhex(
            '4b80eb290000040d003d003d00000000584f584f00000000584f584f000000002900'
            '552904c902a9b78f00010a010d000004200001084000020000065501045608f17c010000000000c897'
        )
        self.assertEqual(build_neutral_stick_packet(0x29eb, 0x4f58, 0x3d00, 0x8fb7, 0x17cf1), expected)

    def test_rejects_counter_overflow(self):
        with self.assertRaises(ValueError):
            build_neutral_stick_packet(1, 8, 8, 0, 0x1000000)

    def test_pitch_channel_matches_real_non_neutral_capture(self):
        self.assertEqual(
            build_stick_payload(1024, 1684, 1024, 1024, 0x23aef).hex(),
            '010d0000a4340001084000020000065501045608ef3a020000000000',
        )

    def test_rejects_channel_outside_observed_range(self):
        with self.assertRaises(ValueError):
            build_stick_payload(1024, 1685, 1024, 1024, 0)

    def test_full_pitch_packet_matches_real_capture(self):
        expected = bytes.fromhex(
            '4b804fd500000455801d801d0000000098aa98aa0000000078aa78aa000000002900'
            '552904c902a931d400010a010d0000142a00010840000200000655010456088b3a0200000000003bb4'
        )
        self.assertEqual(
            build_stick_packet(0xd54f, 0xaa78, 0x1d80, 0xd431, 0x23a8b,
                               pitch=1346, video_word=0xaa98), expected,
        )

    def test_labeled_forward_packet_matches_real_capture(self):
        expected = bytes.fromhex(
            '4b80dfeb000004fba0b5a0b500000000f85ef85e00000000f85ef85e000000002900'
            '552904c902a901c500010a010d00007c2a00010840000200000655010456088913100000000000ee1e'
        )
        self.assertEqual(
            build_stick_packet(0xebdf, 0x5ef8, 0xb5a0, 0xc501, 0x101389,
                               pitch=1359), expected,
        )


if __name__ == '__main__':
    unittest.main()
