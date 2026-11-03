import time
import unittest
from unittest.mock import Mock
from test_landing_confirmation import state
from voice_control import VoiceExecutor

class VoiceLandingConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.link=Mock()
        self.link.status.return_value=state()
        self.link.confirm_landing.return_value=b'\0'
        self.link.land.return_value=b'\0'
        self.messages=[]
        self.executor=VoiceExecutor(self.link,Mock(),notify=self.messages.append,circle=Mock(),camera=Mock(),dolly=Mock())
        self.executor.enabled=True

    def tearDown(self):self.executor.close()

    def test_current_land_request_automatically_completes_once(self):
        self.executor.poll_camera()
        self.link.confirm_landing.assert_not_called()
        self.executor.handle('降落')
        self.executor.poll_camera()
        self.link.confirm_landing.assert_called_once_with(confirmed=True,timeout=.4)
        self.executor.poll_camera()
        self.link.confirm_landing.assert_called_once()

    def test_return_to_hover_and_brief_descent_never_trigger_followup(self):
        for field,value in (('mode_raw',6),('descending_duration_s',.3)):
            self.executor.handle('降落')
            self.link.status.return_value['landing_observation'][field]=value
            self.executor.poll_camera()
            self.link.confirm_landing.assert_not_called()

    def test_changed_request_or_stop_cancels_authorization(self):
        self.executor.handle('降落')
        self.link.status.return_value['landing_observation']['requested_at']-=1
        self.executor.poll_camera();self.link.confirm_landing.assert_not_called()
        self.executor.handle('降落');self.executor.handle('停止');self.executor.poll_camera()
        self.link.confirm_landing.assert_not_called()

    def test_manual_land_while_voice_paused_can_complete_without_extra_speech(self):
        self.executor.enabled=False
        self.executor.handle('降落',manual=True)
        self.executor.poll_camera()
        self.link.confirm_landing.assert_called_once()

    def test_no_successful_initial_ack_or_error_does_not_retry(self):
        self.link.land.return_value=None
        self.executor.handle('降落');self.executor.poll_camera()
        self.link.confirm_landing.assert_not_called()
        self.link.land.return_value=b'\0'
        self.link.confirm_landing.side_effect=RuntimeError('not ready')
        self.executor.handle('降落');self.executor.poll_camera();self.executor.poll_camera()
        self.link.confirm_landing.assert_called_once()
