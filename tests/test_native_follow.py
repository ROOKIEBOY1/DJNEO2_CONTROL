import threading
import time
import unittest
from unittest.mock import Mock
from follow_protocol import SELECTION_PAYLOAD, START_PAYLOAD, validate_request
from native_follow import NativeFollow


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.link = Mock(native_task_may_fly=False)
        self.state = dict(device_verified=True, transport_mode='reliable',
            last_downlink_age_s=.01, last_video_age_s=.01, land_requested=False, land_sent=False,
            landing_observation=dict(telemetry_age_s=.01, motors_on=False, airborne_flag=False),
            subject_observation={'task': dict(at=time.time(), phase_raw=0, payload_hex='0000000102')})
        self.link.status.return_value = self.state
        self.link.request_follow.side_effect = lambda r,cs,ci,p,**kw: b'\0'+p if ci==0xf9 else b'\0'
        self.m = NativeFollow(self.link)

    def test_capture_whitelist_rejects_other_tasks_and_nonempty_stop(self):
        for request in [(0x17,3,0xf9,SELECTION_PAYLOAD), (0x92,0x23,0x13,START_PAYLOAD), (0x92,0x23,0x15,b'')]:
            validate_request(*request)
        for request in [(0x92,0x23,0x13,b'\1\5\0'), (0x92,0x23,0x15,b'\0'), (0x92,3,0xf9,b'anything')]:
            with self.assertRaises(ValueError): validate_request(*request)

    def test_prepare_only_selects_mode_and_start_requires_confirmation(self):
        self.m.prepare()
        self.assertEqual(self.link.request_follow.call_args.args, (0x17,3,0xf9,bytes.fromhex('878867a30a000000')))
        self.assertEqual(self.link.request_follow.call_count,1)
        with self.assertRaises(ValueError): self.m.start()
        self.m.start(confirmed=True)
        self.assertEqual(self.link.request_follow.call_args.args,(0x92,0x23,0x13,b'\1\x0a\0'))
        self.assertTrue(self.link.native_task_may_fly)
        self.assertFalse(self.m.status()['executing_observed'])
        with self.assertRaises(RuntimeError): self.m.start(confirmed=True)

    def test_bad_echo_and_stale_or_airborne_state_block_start(self):
        self.link.request_follow.side_effect=None
        self.link.request_follow.return_value=b'\0'
        with self.assertRaises(RuntimeError): self.m.prepare()
        with self.assertRaises(RuntimeError): self.m.start(confirmed=True)
        for change in ('airborne','stale','task'):
            self.setUp()
            if change=='airborne': self.state['landing_observation']['motors_on']=True
            if change=='stale': self.state['last_video_age_s']=2
            if change=='task': self.state['subject_observation']['task']['at']=time.time()-2
            with self.assertRaises(RuntimeError): self.m.prepare()
            self.link.request_follow.assert_not_called()

    def test_unanswered_start_cannot_be_retried(self):
        self.m.prepare()
        self.link.request_follow.side_effect=None
        self.link.request_follow.return_value=None
        self.assertIsNone(self.m.start(confirmed=True))
        self.assertTrue(self.link.native_task_may_fly)
        with self.assertRaises(RuntimeError): self.m.start(confirmed=True)

    def test_cancel_during_prepare_prevents_late_prepared_or_start(self):
        token=threading.Event()
        def request(r,cs,ci,p,**kw):
            token.set()
            return b'\0'+p
        self.link.request_follow.side_effect=request
        with self.assertRaises(RuntimeError): self.m.prepare(cancel_event=token)
        self.assertFalse(self.m.prepared)

    def test_stop_ack_is_not_task_exit_and_land_skips_stop_only_after_fresh_exit(self):
        self.m.prepare(); self.m.start(confirmed=True)
        now=time.time()
        self.m.started_at=now-.1
        self.state['subject_observation']['task']['at']=now-.2
        self.m.stop()
        self.assertEqual(self.link.request_follow.call_args.args,(0x92,0x23,0x15,b''))
        self.assertTrue(self.m.is_active_or_unknown())
        self.state['subject_observation']['task'].update(at=time.time(),phase_raw=0,payload_hex='0000000101')
        self.assertFalse(self.m.is_active_or_unknown())
        self.link.request_follow.reset_mock()
        self.m.land()
        self.link.request_follow.assert_not_called()
        self.link.land.assert_called_once()

    def test_stale_exit_is_unknown_and_landing_survives_stop_failure(self):
        self.m.started_at=time.time()-3
        self.state['subject_observation']['task']['at']=time.time()-2
        self.assertTrue(self.m.is_active_or_unknown())
        self.link.request_follow.side_effect=RuntimeError('stop lost')
        self.m.land()
        self.link.land.assert_called_once()

    def test_controller_only_routes_follow_whitelist_over_tunnel(self):
        from flight_controller import FlightController
        link=object.__new__(FlightController)
        link._stop=threading.Event(); link.device_verified=True
        link.last_receive_at=time.monotonic(); link.land_requested=link.land_sent=False
        link._request_tunneled=Mock(return_value=b'\0')
        self.assertEqual(link.request_follow(0x92,0x23,0x13,START_PAYLOAD),b'\0')
        self.assertEqual(link._request_tunneled.call_args.kwargs['label'],'native_follow')
        with self.assertRaises(ValueError): link.request_follow(0x92,0x23,0x13,b'\1\5\0')
        link.land_requested=True
        with self.assertRaises(RuntimeError): link.request_follow(0x92,0x23,0x13,START_PAYLOAD)
        link.request_follow(0x92,0x23,0x15,b'')
