import time
import unittest
from unittest.mock import Mock
from dolly_zoom_console import ConsoleActions

class DollyConsoleTests(unittest.TestCase):
    def setUp(self):
        self.mission=Mock(prepared=True)
        self.mission.start.return_value=b'\0'
        self.mission.stop.return_value=b'\0'
        self.mission.land.return_value=b'\0'
        self.messages=[]
        self.console=ConsoleActions(self.mission,self.messages.append)

    def test_start_requires_unexpired_confirmation(self):
        self.console.handle('确认')
        self.mission.start.assert_not_called()
        self.console.handle('滑动变焦')
        self.console.pending_until=time.monotonic()-1
        self.console.handle('确认')
        self.mission.start.assert_not_called()
        self.console.handle('滑动变焦');self.console.handle('确认')
        self.console.worker.join(1)
        self.mission.start.assert_called_once()

    def test_setting_change_invalidates_confirmation_and_preparation(self):
        self.console.handle('滑动变焦')
        self.console.handle('景别全身')
        self.assertFalse(self.mission.prepared)
        self.console.handle('确认')
        self.mission.start.assert_not_called()
        self.assertEqual(self.console.config['framing'],'全身')

    def test_stop_bypasses_busy_worker_and_cancels_token(self):
        self.console.worker=Mock()
        self.console.worker.is_alive.return_value=True
        token=self.console.token
        self.console.handle('停止')
        self.assertTrue(token.is_set())
        self.mission.stop.assert_called_once()

    def test_land_and_exit_clear_pending_start(self):
        self.console.handle('滑动变焦');self.console.handle('降落');self.console.handle('确认')
        self.mission.land.assert_called_once()
        self.mission.start.assert_not_called()
        self.assertFalse(self.console.handle('退出'))

    def test_invalid_settings_are_not_silently_clamped(self):
        for text in ('倍率10','景别特写','渐远随便'):
            self.console.handle(text)
        self.assertEqual(self.console.config,dict(framing='半身',zoom=2,retreat=True))
