from pathlib import Path
import tempfile
import threading
import time
import unittest
from voice_commands import parse_voice_command
from voice_control import VoiceExecutor
from voice_motion import CalibrationStore
from test_voice_control import FakeController

class FakeCircle:
    def __init__(self, link):
        self.link = link
        self.prepared = False
        self.radius_m = 2.
        self.starts = self.stops = 0
        self.running = False
        self.started_at = None
        self.land_requested = False
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
    def prepare(self, radius_m=2., *, cancel_event=None):
        self.entered.set()
        if self.block:
            self.release.wait(1)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('canceled')
        self.radius_m = radius_m
        self.prepared = True
    def start(self, *, confirmed=False, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('canceled')
        assert confirmed
        self.starts += 1
        self.running = True
        self.started_at = time.time()
        return b'\x00'
    def stop(self, timeout=.4):
        self.stops += 1
        self.running = False
        self.release.set()
        return b'\x00'
    def is_active_or_unknown(self):
        return self.running

class VoiceCircleTests(unittest.TestCase):
    def test_takeoff_after_native_landing_uses_new_cycle_and_resets_mission(self):
        from unittest.mock import Mock
        self.executor.enabled = True
        self.link.native_task_may_fly = True
        self.link.takeoff_sent = False
        self.link.status = lambda: {'native_task_may_fly': True,
            'landing_observation': {'telemetry_age_s': .01, 'motors_on': False, 'airborne_flag': False}}
        self.link.takeoff_again = Mock(return_value=b'\x00')
        self.circle.land_requested = True
        self.executor.handle('起飞')
        self.executor.handle('确认')
        self.idle()
        self.link.takeoff_again.assert_called_once()
        self.assertFalse(self.circle.land_requested)

    def test_airborne_takeoff_does_not_send_another_takeoff_or_request_confirmation(self):
        self.executor.enabled = True
        self.link.status = lambda: {'landing_observation': {'telemetry_age_s': .01,
            'motors_on': True, 'airborne_flag': True}}
        self.executor.handle('起飞')
        self.assertIsNone(self.executor._pending)
        self.executor.handle('确认')
        self.idle()
        self.assertEqual(self.link.takeoffs, 0)

    def test_circle_can_begin_a_new_confirmed_grounded_cycle_without_reconnecting(self):
        from unittest.mock import Mock
        self.executor.enabled = True
        self.link.status = lambda: {'native_task_may_fly': True, 'land_sent': True,
            'landing_observation': {'telemetry_age_s': .01, 'motors_on': False, 'airborne_flag': False}}
        self.link.prepare_grounded_cycle = Mock()
        self.circle.land_requested = True
        self.executor.handle('环绕两米')
        self.executor.handle('确认')
        self.idle()
        self.link.prepare_grounded_cycle.assert_called_once_with(confirmed_grounded=True)
        self.assertEqual(self.circle.starts, 1)
        self.assertFalse(self.circle.land_requested)

    def test_circle_meter_abbreviation_is_accepted_for_keyboard(self):
        self.assertEqual(parse_voice_command('环绕2M').amount, 2)

    def test_failed_center_and_task_stop_do_not_suppress_landing(self):
        from unittest.mock import Mock
        self.link.center_sticks = Mock(side_effect=RuntimeError('neutral failed'))
        self.circle.stop = Mock(side_effect=RuntimeError('stop failed'))
        self.link.land = Mock(return_value=b'\x00')
        self.executor._circle_used = True
        self.executor.handle('降落', manual=True)
        self.circle.stop.assert_called_once()
        self.link.land.assert_called_once()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.link = FakeController()
        self.circle = FakeCircle(self.link)
        self.executor = VoiceExecutor(self.link, CalibrationStore(Path(self.directory.name)/'c.json'),
                                      notify=lambda _:None, circle=self.circle)
    def tearDown(self):
        self.executor.close()
        self.directory.cleanup()
    def idle(self):
        limit = time.monotonic()+1
        while self.executor.busy and time.monotonic()<limit:
            time.sleep(.005)
        self.assertFalse(self.executor.busy)
    def test_parser_distinguishes_setting_start_stop_and_rejects_invalid_radius(self):
        for text, kind, radius in [('设置环绕半径五米','circle_radius',5),('环绕半径3.5米','circle_radius',3.5),
                                   ('环绕五米','circle',5),('以五米半径环绕','circle',5),('环绕','circle',0),
                                   ('准备环绕','circle_prepare',0),('停止环绕','circle_stop',0)]:
            intent = parse_voice_command(text)
            self.assertEqual((intent.kind,intent.amount), (kind,radius))
        for text in ('不要环绕','环绕一米','环绕十一米','环绕五秒','环绕五米然后降落'):
            with self.assertRaises(ValueError):
                parse_voice_command(text)
    def test_arming_radius_selection_and_short_confirmation(self):
        self.executor.handle('环绕五米')
        self.executor.handle('确认')
        self.assertEqual(self.circle.starts,0)
        self.executor.handle('启用语音',manual=True)
        self.executor.handle('半径五米')
        self.assertFalse(self.circle.prepared)
        self.executor.handle('环绕')
        self.assertEqual(self.circle.starts,0)
        self.executor.handle('确认')
        self.idle()
        self.assertEqual(self.circle.radius_m,5)
        self.assertEqual(self.circle.starts,1)
        self.executor.handle('确认')
        self.assertEqual(self.circle.starts,1)
    def test_stop_during_preparation_prevents_start(self):
        self.circle.block=True
        self.executor.handle('启用语音',manual=True)
        self.executor.handle('环绕三米')
        self.executor.handle('确认')
        self.assertTrue(self.circle.entered.wait(.5))
        self.executor.handle('停止',confidence=.55)
        self.idle()
        self.assertEqual(self.circle.starts,0)
        self.assertGreater(self.circle.stops,0)
    def test_movement_and_radius_changes_cannot_mix_with_native_mission(self):
        self.executor.handle('启用语音',manual=True)
        self.executor.handle('环绕')
        self.executor.handle('确认')
        self.idle()
        self.executor.handle('前进一点')
        self.executor.handle('半径五米')
        self.assertEqual(self.link.moves,[])
        self.executor.handle('停止环绕',confidence=.55)
        self.assertFalse(self.circle.running)
        self.executor.handle('前进一点')
        self.idle()
        self.assertEqual(self.link.moves,[('forward',.5)])
    def test_radius_change_invalidates_previous_confirmation(self):
        self.executor.handle('启用语音',manual=True)
        self.executor.handle('环绕五米')
        self.executor.handle('半径三米')
        self.executor.handle('确认')
        self.assertEqual(self.circle.starts,0)
