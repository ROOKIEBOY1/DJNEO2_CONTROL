from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from voice_control import VoiceExecutor
from voice_motion import CalibrationStore
from flight_controller import FlightController


class FakeController:
    def __init__(self, block=False):
        self.moves = []
        self.takeoffs = 0
        self.landings = 0
        self.centers = 0
        self.block = block
        self.release = threading.Event()

    def move(self, direction, seconds, *, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            return
        self.moves.append((direction, seconds))
        if self.block:
            self.release.wait(1)

    def center_sticks(self):
        self.centers += 1
        self.release.set()

    def takeoff(self, *, cancel_event=None):
        self.takeoffs += 1
        return b'\x00'

    def takeoff_again(self, *, confirmed_grounded=False, cancel_event=None):
        assert confirmed_grounded
        return self.takeoff(cancel_event=cancel_event)

    def land(self):
        self.landings += 1
        self.center_sticks()
        return b'\x00'

    def status(self):
        return {}


class VoiceControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = CalibrationStore(Path(self.directory.name) / 'calibration.json')
        self.controller = FakeController()
        self.executor = VoiceExecutor(self.controller, self.store, notify=lambda _: None)

    def tearDown(self):
        self.executor.close()
        self.directory.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 1
        while self.executor.busy and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.executor.busy)

    def test_unarmed_low_confidence_and_preview_do_not_execute(self):
        self.executor.handle('向上飞一点', confidence=0.95)
        self.assertEqual(self.controller.moves, [])
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('向上飞一点', confidence=0.2)
        self.assertEqual(self.controller.moves, [])
        preview = VoiceExecutor(None, self.store, notify=lambda _: None)
        preview.handle('向上飞一点', confidence=0.95)
        self.assertFalse(preview.busy)

    def test_armed_motion_executes_and_voice_takeoff_requires_confirmation(self):
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('向上飞一点', confidence=0.95)
        self.wait_idle()
        self.assertEqual(self.controller.moves, [('up', 0.5)])
        self.executor.handle('起飞', confidence=0.95)
        self.assertEqual(self.controller.takeoffs, 0)
        self.executor.handle('确认起飞', confidence=0.95)
        self.wait_idle()
        self.assertEqual(self.controller.takeoffs, 1)
        self.executor.handle('确认起飞', confidence=0.95)
        self.wait_idle()
        self.assertEqual(self.controller.takeoffs, 1)

    def test_stop_cancels_remaining_distance_segments_without_queuing_more_motion(self):
        self.controller.block = True
        self.store.record_sample('forward', 1, distance_m=1)
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('向前飞十米', confidence=0.95)
        deadline = time.monotonic() + 0.5
        while not self.controller.moves and time.monotonic() < deadline:
            time.sleep(0.005)
        self.executor.handle('向后飞一点', confidence=0.95)
        self.executor.handle('停止', confidence=0.95)
        self.wait_idle()
        self.assertEqual(len(self.controller.moves), 1)
        self.assertGreater(self.controller.centers, 0)

    def test_landing_interrupts_active_motion_and_stale_speech_is_ignored(self):
        self.controller.block = True
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('向前飞一点', confidence=0.95, event_time=time.time() - 10)
        self.assertEqual(self.controller.moves, [])
        self.executor.handle('向上飞一点', confidence=0.95)
        deadline = time.monotonic() + 0.5
        while not self.controller.moves and time.monotonic() < deadline:
            time.sleep(0.005)
        self.executor.handle('降落', confidence=0.95)
        self.wait_idle()
        self.assertEqual(self.controller.landings, 1)
        self.assertEqual(len(self.controller.moves), 1)

    def test_keyboard_landing_still_works_when_voice_paused(self):
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('暂停语音', manual=True)
        self.executor.handle('降落', manual=True)
        self.assertEqual(self.controller.landings, 1)

    def test_spoken_enable_cannot_arm(self):
        self.executor.handle('启用语音', confidence=.99)
        self.assertFalse(self.executor.enabled)
        self.executor.handle('向上飞一点', confidence=.99)
        self.assertEqual(self.controller.moves, [])

    def test_cancel_before_controller_move_cannot_be_lost_on_entry(self):
        controller = FlightController.__new__(FlightController)
        controller.takeoff_acknowledged = True
        controller.land_requested = controller.land_sent = False
        controller._stop = threading.Event()
        controller.last_receive_at = time.monotonic()
        controller._movement_lock = threading.Lock()
        controller._stick_tx_lock = threading.RLock()
        controller._lock = threading.RLock()
        controller._send_stick = Mock()
        canceled = threading.Event()
        canceled.set()
        with self.assertRaises(RuntimeError):
            controller.move('up', .5, cancel_event=canceled)
        controller._send_stick.assert_not_called()
        self.assertFalse(controller._movement_lock.locked())
        controller._tx_lock = threading.Lock()
        # A canceled takeoff must also stop at the final serialized send gate.
        # No socket exists here: reaching sendto would fail this assertion.
        with self.assertRaisesRegex(RuntimeError, '命令未发送'):
            controller._send(b'', cancel_event=canceled)

    def test_landing_does_not_wait_for_takeoff_reply(self):
        entered = threading.Event()
        def blocked_takeoff(*, cancel_event=None):
            entered.set()
            self.controller.release.wait(1)
            return b'\x00'
        self.controller.takeoff = blocked_takeoff
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('起飞', manual=True)
        self.assertTrue(entered.wait(.5))
        start = time.monotonic()
        self.executor.handle('降落', manual=True)
        self.assertLess(time.monotonic() - start, .2)
        self.assertEqual(self.controller.landings, 1)

    def test_short_confirmation_handles_takeoff_and_retakeoff_once(self):
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('确认')
        self.assertEqual(self.controller.takeoffs, 0)
        self.executor.handle('起飞')
        self.executor.handle('确认')
        self.wait_idle()
        self.assertEqual(self.controller.takeoffs, 1)
        self.executor.handle('降落')
        self.assertEqual(self.controller.landings, 1)
        self.executor.handle('再次起飞')
        self.assertEqual(self.controller.takeoffs, 1)
        self.executor.handle('确认')
        self.wait_idle()
        self.executor.handle('确认')
        self.assertEqual(self.controller.takeoffs, 2)

    def test_short_confirmation_expiry_and_stop_cancel_pending_takeoff(self):
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('起飞')
        self.executor._pending = ('takeoff', time.monotonic() - 1)
        self.executor.handle('确认')
        self.executor.handle('起飞')
        self.executor.handle('停止')
        self.executor.handle('确认')
        self.assertEqual(self.controller.takeoffs, 0)

    def test_land_and_stop_use_separate_confidence_threshold_from_movement(self):
        self.executor.handle('启用语音', manual=True)
        self.executor.handle('下降五秒', confidence=.61)
        self.assertEqual(self.controller.moves, [])
        self.executor.handle('降落', confidence=.55)
        self.assertEqual(self.controller.landings, 1)
        centers = self.controller.centers
        self.executor.handle('停止', confidence=.55)
        self.assertGreater(self.controller.centers, centers)
        self.executor.handle('降落', confidence=.49)
        self.executor.handle('降落', confidence=.67, event_time=time.time()-10)
        self.executor.handle('不要降落', confidence=.99)
        self.assertEqual(self.controller.landings, 1)


if __name__ == '__main__':
    unittest.main()
