import time
import unittest
from unittest.mock import patch
from video_packets import VideoAssembler

def packet(frame=1, count=1, part=0, payload=b'abc', flags=0, seq=1):
    raw = bytearray(20)
    raw[:2] = (0x8000 | (20 + len(payload))).to_bytes(2, 'little')
    raw[2:4] = b'\x01\x00'
    raw[4:6] = seq.to_bytes(2, 'little')
    raw[6] = 2
    for x in raw[:7]:
        raw[7] ^= x
    raw[16:20] = bytes([frame, count | ((part & 1) << 7), flags | (part >> 1), 0])
    return bytes(raw) + payload

class AssemblyTests(unittest.TestCase):
    def test_odd_fragments_and_metadata_bits_preserved(self):
        a = VideoAssembler()
        self.assertIsNone(a.feed(packet(count=3, part=2, payload=b'C', flags=0x60)))
        self.assertIsNone(a.feed(packet(count=3, part=0, payload=b'A', flags=0x60)))
        self.assertEqual(a.feed(packet(count=3, part=1, payload=b'B', flags=0x60)), b'ABC')

    def test_duplicate_complete_and_incomplete_packets_not_redecoded(self):
        a = VideoAssembler()
        p = packet(count=2, payload=b'A')
        self.assertIsNone(a.feed(p))
        self.assertIsNone(a.feed(p))
        self.assertEqual(a.feed(packet(count=2, part=1, payload=b'B')), b'AB')
        self.assertIsNone(a.feed(p))
        self.assertIsNone(a.feed(packet(count=2, part=1, payload=b'B')))

    def test_wrap_and_lost_frame_dont_block_next_frames(self):
        a = VideoAssembler(max_pending=2)
        self.assertIsNone(a.feed(packet(254, 2)))
        self.assertEqual(a.feed(packet(255, payload=b'x')), b'x')
        self.assertEqual(a.feed(packet(0, payload=b'y')), b'y')
        for frame in (1, 2, 3):
            a.feed(packet(frame, 2))
        self.assertLessEqual(a.pending_count, 2)
        self.assertGreater(a.dropped_frames, 0)

    def test_corrupt_and_invalid_fragment_rejected(self):
        a = VideoAssembler()
        self.assertIsNone(a.feed(b'bad'))
        self.assertIsNone(a.feed(packet(count=0)))
        self.assertIsNone(a.feed(packet(count=2, part=2)))
        p = bytearray(packet()); p[7] ^= 1
        self.assertIsNone(a.feed(p))
        self.assertEqual(a.invalid_packets, 4)

class ExtraAssemblyTests(unittest.TestCase):
    def test_conflicting_duplicate_discards_frame(self):
        a = VideoAssembler()
        a.feed(packet(1, 2, 0, b'A'))
        self.assertIsNone(a.feed(packet(1, 2, 0, b'X')))
        self.assertIsNone(a.feed(packet(1, 2, 1, b'B')))
        self.assertEqual(a.dropped_frames, 1)

    def test_expired_frame_id_can_be_reused_after_full_wrap(self):
        with patch('video_packets.time.monotonic', return_value=0):
            a = VideoAssembler(max_age=1)
            self.assertEqual(a.feed(packet(1, payload=b'A')), b'A')
            a.feed(packet(2, 2))
        with patch('video_packets.time.monotonic', return_value=2):
            self.assertEqual(a.feed(packet(1, payload=b'B')), b'B')
            self.assertEqual(a.dropped_frames, 1)

