import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from diagnose_follow_layers import capture_trial, require_idle, decode_output


class LayerCaptureTests(unittest.TestCase):
    def test_native_output_supports_utf8_and_chinese_legacy_encoding(self):
        text='数据包监视器未运行。'
        for encoding in ('utf-8','gbk'):
            self.assertEqual(decode_output(text.encode(encoding)),text)

    def test_unknown_or_running_capture_cannot_be_taken_over(self):
        for text in ('Packet Monitor is running.','无法查询状态','正在运行'):
            with self.assertRaises(RuntimeError):require_idle(text)
        for text in ('Packet Monitor is not running.','数据包监视器未运行。','已停止'):
            require_idle(text)

    def test_failed_start_does_not_stop_somebody_elses_capture(self):
        calls=[]
        def command(args):
            calls.append(args)
            if args[1]=='status':return 'Packet Monitor is not running.'
            if args[1]=='start':raise RuntimeError('busy')
            return ''
        console=Mock()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):capture_trial(Path(d),command,console)
        self.assertFalse(any(c[1]=='stop' for c in calls))
        console.assert_not_called()

    def test_child_failure_still_saves_own_capture_and_cleans_only_own_filter(self):
        calls=[]
        def command(args):
            calls.append(args)
            return 'Packet Monitor is not running.' if args[1]=='status' else ''
        console=Mock(side_effect=RuntimeError('console failed'))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):capture_trial(Path(d),command,console)
            self.assertTrue(list(Path(d).rglob('session.json')))
        self.assertTrue(any(c[1]=='stop' for c in calls))
        removal=[c for c in calls if c[1:3]==['filter','remove']]
        self.assertEqual(len(removal),1)
        self.assertTrue(removal[0][3].startswith('neo-video-'))
