import contextlib
import io
from unittest.mock import patch, Mock
import unittest
import circle_console
from test_native_circle import FakeLink

class CircleConsoleTests(unittest.TestCase):
    def test_default_preview_never_connects(self):
        with patch('sys.argv', ['circle_console.py']), patch('flight_console.FlightController.connect') as connect:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                circle_console.main()
        connect.assert_not_called()
        self.assertIn('预览', output.getvalue())
        self.assertIn('自动起飞', output.getvalue())

    def test_confirmation_without_parameter_preparation_cannot_start(self):
        link = FakeLink()
        link.initialize_control_link = lambda: True
        link.close = Mock()
        link.save_log = Mock(return_value='mock-log-only')
        routing = Mock()
        with patch('sys.argv', ['circle_console.py', '--connect']), \
             patch('flight_console.FlightController.connect', return_value=link), \
             patch('flight_console.detect_local_ip', return_value='127.0.0.1'), \
             patch('routing_sequences.RoutingSequenceStore', return_value=routing), \
             patch('builtins.input', side_effect=['环绕', '确认', '退出']), \
             contextlib.redirect_stdout(io.StringIO()):
            circle_console.main()
        self.assertEqual(link.calls, [])
        self.assertFalse(link.native_task_may_fly)
        link.close.assert_called_once()
        routing.close.assert_called_once()
