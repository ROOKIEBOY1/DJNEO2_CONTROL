import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from video_packet_trace import VideoPacketTrace, packet_records


class PacketTraceTests(unittest.TestCase):
    def test_packet_and_queue_decision_survive_binary_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sample.udp.bin'
            trace=VideoPacketTrace(p)
            trace.submit(b'accepted',True,at=10.)
            trace.submit(b'queue-lost',False,at=11.)
            trace.close()
            self.assertEqual(list(packet_records(p)),[(10.,True,b'accepted'),(11.,False,b'queue-lost')])
            state=json.loads(trace.summary_path.read_text())
            self.assertTrue(state['closed'])
            self.assertEqual(state['records'],2)
            self.assertEqual(state['queue_drops'],0)

    def test_limit_is_explicit_and_not_silently_complete(self):
        with tempfile.TemporaryDirectory() as d:
            trace=VideoPacketTrace(Path(d)/'sample.udp.bin',max_bytes=20)
            trace.submit(b'longer-than-remaining-header-budget',True)
            trace.close()
            self.assertTrue(trace.status()['capped'])
            self.assertEqual(list(packet_records(trace.path)),[])

    def test_truncated_binary_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.udp.bin';p.write_bytes(b'partial')
            with self.assertRaises(ValueError):list(packet_records(p))

    def test_optional_trace_does_not_break_video_submission(self):
        from video_stream import VideoReceiver
        class Decoder:
            def feed(self,data):return []
        with tempfile.TemporaryDirectory() as d, patch('video_stream.HEVCDecoder',return_value=Decoder()):
            receiver=VideoReceiver(preview=False,diagnostics_dir=d)
            try:
                receiver._packet_trace.submit=lambda *a,**kw: (_ for _ in ()).throw(OSError('disk failed'))
                self.assertTrue(receiver.submit(b'test'))
            finally:receiver.close()
