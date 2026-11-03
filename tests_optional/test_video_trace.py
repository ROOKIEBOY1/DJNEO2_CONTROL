import json
from pathlib import Path
import tempfile
import unittest
from video_trace import VideoTrace

class VideoTraceTests(unittest.TestCase):
    def test_triggering_bytes_are_flushed_before_decoder_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            trace=VideoTrace(directory)
            try:
                trace.begin(b'pending-codec-frame')
                self.assertEqual(trace.stream_path.read_bytes(),b'pending-codec-frame')
            finally:trace.close()

    def test_raw_stream_and_decoder_progress_are_bounded_and_replayable(self):
        with tempfile.TemporaryDirectory() as directory:
            trace=VideoTrace(directory,max_bytes=6)
            trace.record(b'abc',dict(decoded=1,warnings=0))
            trace.record(b'def',dict(decoded=0,warnings=3))
            trace.record(b'ghi',dict(decoded=0,warnings=6))
            trace.close()
            state=trace.status()
            self.assertEqual(Path(state['stream_path']).read_bytes(),b'abcdef')
            self.assertTrue(state['capped'])
            records=[json.loads(x) for x in Path(state['index_path']).read_text().splitlines()]
            self.assertEqual([x['offset'] for x in records],[0,3])
            self.assertEqual([x['decoded'] for x in records],[1,0])
            summary=json.loads(trace.stream_path.with_suffix('.hevc.summary.json').read_text())
            self.assertTrue(summary['capped'])
            self.assertTrue(summary['closed'])
