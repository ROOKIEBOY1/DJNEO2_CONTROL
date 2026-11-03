import threading
import time
import unittest
from unittest.mock import Mock
from native_dolly_zoom import NativeDollyZoom
from voice_control import VoiceExecutor

class DollyLandingHandoffTests(unittest.TestCase):
    def test_native_land_does_not_restop_fresh_completed_task(self):
        link=Mock()
        task=dict(at=time.time(),payload_hex='0000000102')
        link.status.return_value=dict(subject_observation={'task':task})
        mission=NativeDollyZoom(link,Mock());mission.started_at=time.time()-10
        mission.land()
        link.request_dolly_zoom.assert_not_called()
        link.land.assert_called_once()

    def test_unknown_or_executing_task_is_stopped_before_land(self):
        for payload,age in (('050d000100',0),('0000000102',2)):
            link=Mock()
            link.status.return_value=dict(subject_observation={'task':dict(at=time.time()-age,payload_hex=payload)})
            mission=NativeDollyZoom(link,Mock());mission.started_at=time.time()-10
            mission.land()
            link.request_dolly_zoom.assert_called_once()
            link.land.assert_called_once()

    def test_voice_land_and_exit_do_not_restop_a_completed_dolly(self):
        link=Mock();link.land.return_value=b'\0'
        link.status.return_value={}
        dolly=Mock(started_at=time.time()-10)
        dolly.stop.return_value=b'\0'
        dolly.is_active_or_unknown.return_value=False
        executor=VoiceExecutor(link,Mock(),notify=lambda _:None,circle=Mock(),camera=Mock(),dolly=dolly)
        executor._dolly_used=True
        executor.handle('降落',manual=True)
        executor.close()
        dolly.stop.assert_not_called()
        link.land.assert_called_once()

    def test_skip_stopping_still_cancels_prepare_token(self):
        link=Mock();link.land.return_value=b'\0';link.status.return_value={}
        dolly=Mock(started_at=time.time()-10)
        dolly.stop.return_value=b'\0'
        dolly.is_active_or_unknown.return_value=False
        executor=VoiceExecutor(link,Mock(),notify=lambda _:None,circle=Mock(),camera=Mock(),dolly=dolly)
        executor._dolly_used=True
        token=executor._cancel
        executor.handle('降落',manual=True)
        self.assertTrue(token.is_set())
        executor.close()
