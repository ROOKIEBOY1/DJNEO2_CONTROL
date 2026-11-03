import threading
import time
import unittest
from unittest.mock import Mock
from flight_controller import FlightController


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.link = FlightController(Mock(), '127.0.0.1', 9003, 1, 8, 1, 1, 1, .05, None)
        self.link.device_verified = True
        self.link.last_video_at = time.monotonic()
        self.link._flight_command = Mock(return_value=b'\x00')

    def telemetry(self, airborne, *, age=0, mode=None):
        now = time.time() - age
        self.link.landing_status.latest = dict(at=now, motors_on=airborne, airborne_flag=airborne,
            mode_raw=(6 if mode is None else mode), flags_raw=12 if airborne else 0)
        self.link.subject_observation.latest['task'] = dict(at=now, payload_hex='0000000101')

    def test_landed_native_flight_can_restart_without_successful_land_ack(self):
        self.link.native_task_may_fly = True
        self.link.native_started_at = time.time() - 5
        self.link.land_requested = self.link.land_sent = True
        self.link.land_acknowledged = False  # 02 when already grounded
        self.telemetry(False)
        self.assertEqual(self.link.takeoff_again(confirmed_grounded=True, timeout=.01), b'\x00')
        self.assertFalse(self.link.native_task_may_fly)
        self.assertFalse(self.link.land_sent)
        self.assertTrue(self.link.takeoff_acknowledged)

    def test_stale_or_airborne_telemetry_cannot_unlock_native_restart(self):
        self.link.native_task_may_fly = True
        self.link.land_sent = self.link.land_acknowledged = True
        for airborne, age in ((False, 2), (True, 0)):
            self.telemetry(airborne, age=age)
            with self.assertRaises(RuntimeError):
                self.link.takeoff_again(confirmed_grounded=True)
        self.link._flight_command.assert_not_called()

    def test_native_stop_requires_new_hover_and_task_telemetry_for_manual_motion(self):
        self.link.native_task_may_fly = True
        self.link.native_started_at = time.time() - 3
        self.telemetry(True)
        old = self.link.landing_status.latest['at']
        self.link.native_stop_confirmed_at = old + .001
        with self.assertRaises(RuntimeError):
            self.link.move('forward', .02)
        time.sleep(.003)
        self.telemetry(True)
        self.link.move('forward', .02)
        self.assertTrue(self.link.movements[-1]['completed'])
        self.assertFalse(self.link.takeoff_acknowledged)
        self.assertTrue(self.link.native_task_may_fly)  # retain close-time landing

    def test_grounded_cycle_reset_is_explicit_and_requires_fresh_motors_off(self):
        self.link.native_task_may_fly = True
        self.link.land_sent = True
        self.telemetry(False)
        self.link.prepare_grounded_cycle(confirmed_grounded=True)
        self.assertFalse(self.link.native_task_may_fly)
        self.assertFalse(self.link.land_sent)
        self.link._flight_command.assert_not_called()

    def test_waiting_native_takeoff_on_ground_cannot_be_reset_as_a_completed_cycle(self):
        self.link.native_task_may_fly = True
        self.link.native_started_at = time.time() - .1
        self.telemetry(False)
        self.link.subject_observation.latest['task']['payload_hex'] = '0105000100'
        with self.assertRaises(RuntimeError):
            self.link.prepare_grounded_cycle(confirmed_grounded=True)
        self.assertTrue(self.link.native_task_may_fly)

    def test_old_stop_ack_cannot_unlock_a_new_native_start(self):
        self.link.native_task_may_fly = True
        self.link.native_stop_confirmed_at = time.time() - 2
        self.link.native_started_at = time.time() - 1
        self.telemetry(True)
        with self.assertRaises(RuntimeError):
            self.link.move('forward', .02)

    def test_rejected_restart_keeps_the_active_landing_watchdog(self):
        timer = self.link._watchdog = Mock()
        self.telemetry(True)
        with self.assertRaises(RuntimeError):
            self.link.prepare_grounded_cycle(confirmed_grounded=True)
        timer.cancel.assert_not_called()
        self.assertIs(self.link._watchdog, timer)

    def test_concurrent_landing_during_takeoff_link_check_prevents_takeoff(self):
        entered, release = threading.Event(), threading.Event()
        check = self.link._check_takeoff_link
        def paused():
            check()
            entered.set()
            release.wait(1)
        self.link._check_takeoff_link = paused
        errors = []
        def takeoff():
            try:
                self.link.takeoff(timeout=.01)
            except RuntimeError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=takeoff)
        worker.start()
        try:
            self.assertTrue(entered.wait(.5))
            self.link.land(timeout=.01)
        finally:
            release.set()
            worker.join(1)
        self.assertEqual([c.args[0] for c in self.link._flight_command.call_args_list], [2])
        self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
