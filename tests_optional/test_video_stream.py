import threading
import time
import unittest
from unittest.mock import patch

from video_stream import VideoAssembler, VideoReceiver


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


class ReceiverTests(unittest.TestCase):
    def test_blocked_decoder_cannot_block_submit_and_queue_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        class Decoder:
            def feed(self, data):
                entered.set(); release.wait(2)
                raise ValueError('broken codec')
        with patch('video_stream.HEVCDecoder', return_value=Decoder()):
            receiver = VideoReceiver(preview=False, queue_size=2)
            try:
                receiver.submit(packet())
                self.assertTrue(entered.wait(1))
                before = time.monotonic()
                for f in range(2, 100): receiver.submit(packet(f))
                self.assertLess(time.monotonic() - before, .3)
                self.assertLessEqual(receiver.status()['queued_packets'], 2)
                self.assertGreater(receiver.status()['queue_drops'], 0)
                release.set()
                until = time.monotonic() + 2
                while not receiver.status()['decode_errors'] and time.monotonic() < until:
                    time.sleep(.01)
                self.assertGreater(receiver.status()['decode_errors'], 0)
            finally:
                release.set(); receiver.close()
            self.assertFalse(receiver.submit(packet()))

    def test_preview_serves_only_local_latest_jpeg(self):
        from urllib.request import urlopen
        class Decoder:
            def feed(self, data): return [b'\xff\xd8image\xff\xd9']
        with patch('video_stream.HEVCDecoder', return_value=Decoder()):
            receiver = VideoReceiver()
            try:
                url = receiver.start()
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                self.assertEqual(url, receiver.start())
                self.assertIn(b'<img', urlopen(url, timeout=2).read())
                receiver.submit(packet())
                until = time.monotonic() + 2
                while not receiver.status()['decoded_frames'] and time.monotonic() < until: time.sleep(.01)
                self.assertEqual(urlopen(url + 'frame.jpg', timeout=2).read(), b'\xff\xd8image\xff\xd9')
            finally: receiver.close()



class ExtraAssemblyTests(unittest.TestCase):
    def test_conflicting_duplicate_discards_frame(self):
        a = VideoAssembler()
        a.feed(packet(1, 2, 0, b'A'))
        self.assertIsNone(a.feed(packet(1, 2, 0, b'X')))
        self.assertIsNone(a.feed(packet(1, 2, 1, b'B')))
        self.assertEqual(a.dropped_frames, 1)

    def test_expired_frame_id_can_be_reused_after_full_wrap(self):
        with patch('video_stream.time.monotonic', return_value=0):
            a = VideoAssembler(max_age=1)
            self.assertEqual(a.feed(packet(1, payload=b'A')), b'A')
            a.feed(packet(2, 2))
        with patch('video_stream.time.monotonic', return_value=2):
            self.assertEqual(a.feed(packet(1, payload=b'B')), b'B')
            self.assertEqual(a.dropped_frames, 1)


class CaptureDecoderTests(unittest.TestCase):
    def test_local_capture_tool_saves_decoded_jpegs_and_stops_at_limit(self):
        import tempfile
        from pathlib import Path
        from decode_video_capture import decode_capture
        records = [(0, 'app', packet()), (0, 'plane', packet(payload=b'A')),
                   (1, 'plane', packet(2, payload=b'B'))]
        class Decoder:
            def feed(self, data): return [b'jpeg-' + data]
        with tempfile.TemporaryDirectory() as tmp, \
             patch('decode_video_capture.udp_records', return_value=iter(records)), \
             patch('decode_video_capture.HEVCDecoder', return_value=Decoder()):
            result = decode_capture('unused.pcapng', tmp, frames=1)
            self.assertEqual(result['decoded_frames'], 1)
            self.assertEqual((Path(tmp) / 'frame-0001.jpg').read_bytes(), b'jpeg-A')
            self.assertFalse((Path(tmp) / 'frame-0002.jpg').exists())






class TimingParameterTests(unittest.TestCase):
    PARAMS = bytes.fromhex('0000000140010c01ffff21600000030000030000030000030096ac0c00000300040000030079400000000142010121600000030000030000030000030096a00280802d1ffaeedc9ae5d4d404040410000003001000000301e080000000014401c17312240890')
    SLOW = bytes.fromhex('0000000140010c01ffff21600000030000030000030000030096ac0c0000030004000003003d400000000142010121600000030000030000030000030096a00280802d1ffaeedc9ae5d4d404040410000003001000000300f080000000014401c17312240890')

    def test_only_timing_changes_are_suppressed(self):
        from video_stream import _TimingParameterFilter
        f = _TimingParameterFilter()
        self.assertEqual(f.feed(self.PARAMS), self.PARAMS)
        # VPS and SPS change only tick rate. PPS still reaches the decoder.
        self.assertEqual(f.feed(self.SLOW), bytes.fromhex('000000014401c17312240890'))
        changed = bytearray(self.SLOW)
        changed[48] ^= 1  # profile/compatibility, outside timing fields
        self.assertIn(bytes(changed)[41:45], f.feed(bytes(changed)))
        self.assertEqual(f.suppressed, 3)

    def test_unsupported_parameter_invalidates_cached_signature(self):
        from video_stream import _TimingParameterFilter
        f = _TimingParameterFilter()
        f.feed(self.PARAMS)
        bad = bytes.fromhex('000000014201ff')
        self.assertEqual(f.feed(bad), bad)
        self.assertIn(bytes.fromhex('000000014201'), f.feed(self.PARAMS))

    def test_metadata_only_repeat_never_flushes_parser(self):
        import sys
        from unittest.mock import Mock
        from video_stream import HEVCDecoder
        codec = Mock()
        codec.parse.return_value = []
        fake_av = Mock()
        fake_av.logging.get_level.return_value = None
        fake_av.CodecContext.create.return_value = codec
        with patch.dict(sys.modules, av=fake_av):
            decoder = HEVCDecoder()
            # First feed is suppressed by the filter: no parse(b'') call.
            decoder._parameters.feed = lambda _: b''
            self.assertEqual(decoder.feed(b'metadata'), [])
            codec.parse.assert_not_called()


class FreezeStatusTests(unittest.TestCase):
    def test_freeze_and_receive_stopped_are_distinct(self):
        class Decoder:
            def feed(self, data): return []
        with patch('video_stream.HEVCDecoder', return_value=Decoder()):
            receiver = VideoReceiver(preview=False)
            try:
                with patch('video_stream.time.monotonic', return_value=10):
                    receiver._last_frame = 1
                    receiver._last_receive = 10
                    self.assertEqual(receiver.status()['state'], 'frozen')
                    receiver._last_receive = 1
                    self.assertEqual(receiver.status()['state'], 'receive_stopped')
                    receiver._last_receive = 10
                    receiver._last_frame = None
                    self.assertEqual(receiver.status()['state'], 'waiting_decode')
            finally: receiver.close()


if __name__ == '__main__': unittest.main()
