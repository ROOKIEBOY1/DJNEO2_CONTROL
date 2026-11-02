"""Regression and corruption checks against the real captured album response."""
import unittest
from pathlib import Path
from capture_messages import udp_records
from control_packets import valid_frames
from offline_takeoff import encode_frame
import media_directory as m


class DirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'captures/camera-media-20261030-141959.pcapng'
        stream = b''.join(raw[20:] for at, side, raw in udp_records(path)
                          if side == 'plane' and raw[6] == 3 and 1793341283.5 < at < 1793341283.6)
        cls.frames = list(valid_frames(stream))

    def test_real_album_independent_of_http(self):
        entries = m.parse_directory_frames(self.frames, 2)
        self.assertEqual(len(entries), 34)
        self.assertEqual(entries[0]['filename'], 'DJI_20261030142114_0034_D.JPG')
        self.assertEqual(entries[1]['path'], 'DCIM/DJI_001/DJI_20261030142058_0033_D.MP4')
        self.assertEqual(entries[0]['size'], 5636096)
        self.assertEqual(entries[1]['size'], 61317634)
        self.assertEqual(sum(entry['size'] is not None for entry in entries), 33)
        self.assertIsNone(entries[-1]['size'])  # Older variable metadata variant.
        self.assertEqual(entries[-1]['filename'], 'DJI_20261026165256_0001_D.MP4')

    def test_reorder_and_identical_duplicate(self):
        self.assertEqual(m.parse_directory_frames(self.frames[::-1] + self.frames[:1], 2),
                         m.parse_directory_frames(self.frames, 2))

    def test_message_sequence_can_continue_while_album_fragments_restart_at_zero(self):
        # A fresh album transfer resets its payload fragment index, while the
        # enclosing DUML message counter can continue from a previous transfer.
        frames=[encode_frame(f[4],f[5],(101+index)&0xffff,f[8],f[9],f[10],f[11:-2])
                for index,f in enumerate(self.frames)]
        self.assertEqual(m.parse_directory_frames(frames,2),m.parse_directory_frames(self.frames,2))

    def test_missing_fragment_session_and_crc_rejected(self):
        for frames, session in ((self.frames[:-1], 2), (self.frames, 1),
                                ([self.frames[0][:-1] + bytes([self.frames[0][-1] ^ 1])] + self.frames[1:], 2)):
            with self.assertRaises(ValueError):
                m.parse_directory_frames(frames, session)

    def test_observed_request_only(self):
        self.assertEqual(m.directory_request_payload().hex(),
                         '4a002110020000000000010000402d000d0100ffffffffffffffff000100000000')
        self.assertEqual(m.make_directory_request(1).hex(),
                         '4a002110010000000000010000402d000d0100ffffffffffffffff000100000000')


if __name__ == '__main__':
    unittest.main()
