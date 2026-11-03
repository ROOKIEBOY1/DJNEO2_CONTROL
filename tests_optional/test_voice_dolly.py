import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from voice_commands import parse_voice_command
from voice_control import VoiceExecutor
from voice_motion import CalibrationStore
from offline_speech import grammar_phrases
from test_voice_control import FakeController
from test_voice_circle import FakeCircle

class FakeDolly(FakeCircle):
    def prepare(self,framing='半身',zoom=2,retreat=True,*,cancel_event=None):
        super().prepare(cancel_event=cancel_event)
        self.config=dict(framing=framing,zoom=zoom,retreat=retreat)

class VoiceDollyTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.link=FakeController()
        self.link.status=lambda:dict(landing_observation=dict(telemetry_age_s=.01,motors_on=False,airborne_flag=False))
        self.dolly=FakeDolly(self.link)
        self.circle=FakeCircle(self.link)
        self.messages=[]
        self.executor=VoiceExecutor(self.link,CalibrationStore(Path(self.directory.name)/'cal.json'),
            notify=self.messages.append,circle=self.circle,dolly=self.dolly)
    def tearDown(self):
        self.executor.close();self.directory.cleanup()
    def idle(self):
        until=time.monotonic()+1
        while self.executor.busy and time.monotonic()<until:time.sleep(.005)
        self.assertFalse(self.executor.busy)
    def arm(self):self.executor.handle('启用语音',manual=True)

    def test_parser_and_offline_grammar_cover_settings_start_and_stop(self):
        examples={'滑动变焦':'dolly','准备滑动变焦':'dolly_prepare','停止滑动变焦':'dolly_stop',
                  '景别全身':'dolly_framing','倍率三倍':'dolly_zoom','渐远关闭':'dolly_retreat'}
        grammar=grammar_phrases()
        for text,kind in examples.items():
            self.assertEqual(parse_voice_command(text).kind,kind)
            self.assertIn(text,grammar)
        for text in ('不要滑动变焦','倍率四倍','滑动变焦然后前进'):
            with self.assertRaises(ValueError):parse_voice_command(text)

    def test_settings_and_single_confirmation_prepare_then_start(self):
        self.executor.handle('滑动变焦');self.executor.handle('确认')
        self.assertEqual(self.dolly.starts,0)
        self.arm()
        for text in ('景别全身','倍率三倍','渐远关闭','滑动变焦'):self.executor.handle(text)
        self.assertEqual(self.dolly.starts,0)
        self.executor.handle('确认');self.idle()
        self.assertEqual(self.dolly.starts,1)
        self.assertEqual(self.dolly.config,dict(framing='全身',zoom=3,retreat=False))
        self.executor.handle('确认');self.idle()
        self.assertEqual(self.dolly.starts,1)

    def test_setting_change_and_timeout_invalidate_confirmation(self):
        self.arm();self.executor.handle('滑动变焦');self.executor.handle('景别远景');self.executor.handle('确认')
        self.assertEqual(self.dolly.starts,0)
        self.executor.handle('滑动变焦')
        pending=list(self.executor._pending);pending[1]=time.monotonic()-1;self.executor._pending=tuple(pending)
        self.executor.handle('确认');self.assertEqual(self.dolly.starts,0)

    def test_stop_during_prepare_is_priority_and_blocks_start(self):
        self.arm();self.dolly.block=True
        self.executor.handle('滑动变焦');self.executor.handle('确认')
        self.assertTrue(self.dolly.entered.wait(.5))
        self.executor.handle('停止滑动变焦',confidence=.55)
        self.idle();self.assertEqual(self.dolly.starts,0);self.assertGreater(self.dolly.stops,0)

    def test_active_dolly_blocks_motion_circle_and_camera(self):
        self.arm();self.executor.handle('滑动变焦');self.executor.handle('确认');self.idle()
        for text in ('前进一秒','环绕','拍照'):self.executor.handle(text)
        self.assertEqual(self.link.moves,[]);self.assertEqual(self.circle.starts,0)

    def test_land_still_attempted_if_dolly_stop_fails(self):
        self.executor._dolly_used=True
        self.dolly.stop=Mock(side_effect=RuntimeError('stop failed'))
        self.executor.handle('降落',manual=True)
        self.assertEqual(self.link.landings,1)

    def test_confirmed_landed_restart_resets_both_missions(self):
        self.arm()
        self.link.prepare_grounded_cycle=Mock()
        self.link.status=lambda:dict(native_task_may_fly=True,land_sent=True,
            landing_observation=dict(telemetry_age_s=.01,motors_on=False,airborne_flag=False))
        self.dolly.land_requested=True;self.dolly.started_at=time.time()-20
        self.executor.handle('滑动变焦');self.executor.handle('确认');self.idle()
        self.link.prepare_grounded_cycle.assert_called_once_with(confirmed_grounded=True)
        self.assertFalse(self.dolly.land_requested);self.assertEqual(self.dolly.starts,1)

    def test_airborne_dolly_is_rejected_before_confirmation(self):
        self.arm()
        self.link.status=lambda:dict(landing_observation=dict(telemetry_age_s=.01,motors_on=True,airborne_flag=True))
        self.executor.handle('滑动变焦');self.executor.handle('确认');self.idle()
        self.assertEqual(self.dolly.starts,0)

    def test_confirmed_grounded_dolly_can_replace_prior_circle_cycle(self):
        self.arm();self.circle.running=True
        self.link.prepare_grounded_cycle=Mock()
        self.link.status=lambda:dict(native_task_may_fly=True,
            landing_observation=dict(telemetry_age_s=.01,motors_on=False,airborne_flag=False))
        self.executor.handle('滑动变焦');self.executor.handle('确认');self.idle()
        self.assertEqual(self.dolly.starts,1)
        self.link.prepare_grounded_cycle.assert_called_once()

    def test_pause_invalidates_confirmation_and_failed_prepare_does_not_start(self):
        self.arm();self.executor.handle('滑动变焦');self.executor.handle('暂停语音');self.executor.handle('确认')
        self.assertEqual(self.dolly.starts,0)
        self.arm();self.dolly.prepare=Mock(side_effect=RuntimeError('bad echo'))
        self.executor.handle('滑动变焦');self.executor.handle('确认');self.idle()
        self.assertEqual(self.dolly.starts,0)
