import time
import threading
import unittest
from unittest.mock import Mock, patch
from follow_console import ConsoleActions


class FollowConsoleTests(unittest.TestCase):
    def setUp(self):
        self.mission=Mock(prepared=True)
        self.mission.start.return_value=b'\0'
        self.mission.land.return_value=b'\0'
        self.mission.stop.return_value=b'\0'
        self.mission.link.confirm_landing.return_value=b'\0'
        self.state={'landing_observation': {'requested_at': 123}}
        self.mission.link.status.return_value=self.state
        self.messages=[]
        self.actions=ConsoleActions(self.mission,self.messages.append)

    def test_follow_needs_fresh_confirmation(self):
        self.actions.handle('跟随')
        self.mission.start.assert_not_called()
        self.actions.handle('确认'); self.actions.worker.join(1)
        self.mission.start.assert_called_once()
        self.actions.handle('确认')
        self.assertIn('没有有效',self.messages[-1])

    def test_expired_confirmation_and_stop_cancel_start(self):
        self.actions.handle('跟随');self.actions.pending_until=time.monotonic()-1
        self.actions.handle('确认');self.mission.start.assert_not_called()
        self.actions.handle('跟随');self.actions.handle('停止');self.actions.handle('确认')
        self.mission.start.assert_not_called();self.mission.stop.assert_called_once()

    def test_stop_and_land_are_available_while_worker_waits(self):
        release=threading.Event()
        self.actions.worker=threading.Thread(target=lambda:release.wait(1))
        self.actions.worker.start()
        try:
            self.actions.handle('降落');self.mission.land.assert_called_once()
        finally:release.set();self.actions.worker.join(1)

    @patch('follow_console.eligible_landing_confirmation',return_value=True)
    def test_landing_followup_only_for_own_acked_land_and_only_once(self,eligible):
        self.actions.poll();self.mission.link.confirm_landing.assert_not_called()
        self.actions.handle('降落');self.actions.poll();self.actions.poll()
        self.mission.link.confirm_landing.assert_called_once_with(confirmed=True,timeout=.2)
        self.actions.handle('停止');self.actions.poll()
        self.mission.link.confirm_landing.assert_called_once()

    @patch('follow_console.eligible_landing_confirmation',return_value=False)
    def test_ineligible_or_failed_landing_does_not_confirm(self,eligible):
        self.actions.handle('降落');self.actions.poll()
        self.mission.link.confirm_landing.assert_not_called()
        self.mission.land.return_value=None
        self.actions.handle('降落');self.actions.poll()
        self.mission.link.confirm_landing.assert_not_called()

    @patch('follow_console.eligible_landing_confirmation',return_value=True)
    def test_cancel_and_changed_request_remove_followup_authorization(self,eligible):
        self.actions.handle('降落');self.actions.cancel();self.actions.poll()
        self.mission.link.confirm_landing.assert_not_called()
        self.actions.handle('降落');self.state['landing_observation']['requested_at']=124
        self.actions.poll();self.mission.link.confirm_landing.assert_not_called()

    def test_landing_phase_feedback_uses_real_status_shape(self):
        from landing_status import FEEDBACK
        self.state['landing_observation'].update(requested_at=123,phase='motors_off')
        self.actions.poll();self.actions.poll()
        self.assertEqual(self.messages,[FEEDBACK['motors_off']])

    def test_connection_failure_is_reported_immediately_once_without_auto_commands(self):
        self.state.update(error='[WinError 10049] address invalid',last_downlink_age_s=2)
        self.actions.poll();self.actions.poll()
        self.assertEqual(len(self.messages),1)
        self.assertIn('10049',self.messages[0])
        self.assertIn('接管',self.messages[0])
        self.mission.land.assert_not_called()
        self.mission.stop.assert_not_called()

    def test_failed_stop_keeps_input_and_error_evidence(self):
        self.mission.stop.side_effect=RuntimeError('connection closed')
        self.actions.handle('停止')
        events=list(self.actions.events)
        self.assertTrue(any(e.get('input')=='停止' for e in events))
        self.assertTrue(any('connection closed' in e.get('message','') for e in events))
