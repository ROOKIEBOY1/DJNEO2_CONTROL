import threading
import time
import unittest
from unittest.mock import Mock
from native_dolly_zoom import NativeDollyZoom
from dolly_zoom_protocol import selection_payload, parameter_writes, validate_request

class DollyTests(unittest.TestCase):
    def setUp(self):
        self.link=Mock()
        self.link.status.side_effect=lambda:dict(device_verified=True,transport_mode='reliable',
            last_downlink_age_s=.01,last_video_age_s=.01,land_requested=False,land_sent=False,
            camera=dict(at=time.time(),age_s=.01,mode_raw=1,time_synced=True,recording_candidate=False),
            landing_observation=dict(at=time.time(),telemetry_age_s=.01,motors_on=False,airborne_flag=False),
            subject_observation={'task':dict(at=time.time(),phase_raw=0,mode_raw=0,payload_hex='0000000102')})
        self.link.request_dolly_zoom.side_effect=lambda receiver,cs,ci,payload,**kw: b'\0'+payload if ci==0xf9 else b'\0'
        self.camera=Mock()
        self.mission=NativeDollyZoom(self.link,self.camera)

    def test_prepare_sends_observed_profile_without_flight(self):
        self.mission.prepare('全身',3,False)
        self.assertTrue(self.mission.prepared)
        calls=[c.args for c in self.link.request_dolly_zoom.call_args_list]
        self.assertEqual(calls[0],(0x92,0x23,0x15,b''))
        self.assertEqual(calls[1],(0x17,3,0xf9,selection_payload()))
        self.assertEqual(calls[2:],[(0x92,3,0xf9,p) for p in parameter_writes('全身',3,False)])
        self.camera.prepare_video_mode.assert_called_once()
        self.assertFalse(any(c[1:3]==(0x23,0x13) for c in calls))

    def test_bad_echo_blocks_prepared_and_start(self):
        self.link.request_dolly_zoom.return_value=None
        self.link.request_dolly_zoom.side_effect=None
        with self.assertRaises(RuntimeError):self.mission.prepare()
        with self.assertRaises(RuntimeError):self.mission.start(confirmed=True)

    def test_confirmed_start_is_not_execution_and_duplicate_is_blocked(self):
        self.mission.prepare()
        with self.assertRaises(ValueError):self.mission.start()
        self.assertEqual(self.mission.start(confirmed=True),b'\0')
        self.assertTrue(self.link.native_task_may_fly)
        self.assertEqual(self.link.request_dolly_zoom.call_args.args,(0x92,0x23,0x13,b'\x01\x0d\x00'))
        self.assertFalse(self.mission.status()['executing_observed'])
        with self.assertRaises(RuntimeError):self.mission.start(confirmed=True)

    def test_stale_airborne_or_active_task_prevents_preparation(self):
        for change in ('stale','airborne','active'):
            state=self.link.status()
            if change=='stale':state['landing_observation']['telemetry_age_s']=2
            if change=='airborne':state['landing_observation']['motors_on']=True
            if change=='active':state['subject_observation']['task']['phase_raw']=5
            self.link.status.side_effect=None;self.link.status.return_value=state
            with self.assertRaises(RuntimeError):self.mission.prepare()
            self.link.request_dolly_zoom.assert_not_called()
            self.setUp()

    def test_stop_during_prepare_prevents_late_start_or_parameters(self):
        entered,release=threading.Event(),threading.Event()
        def request(receiver,cs,ci,payload,**kw):
            if receiver==0x17:entered.set();release.wait(1)
            return b'\0'+payload if ci==0xf9 else b'\0'
        self.link.request_dolly_zoom.side_effect=request
        errors=[]
        def prepare():
            try:self.mission.prepare()
            except RuntimeError as exc:errors.append(str(exc))
        worker=threading.Thread(target=prepare);worker.start()
        try:
            self.assertTrue(entered.wait(.5));self.mission.stop()
        finally:release.set();worker.join(1)
        self.assertTrue(errors)
        self.assertFalse(self.mission.prepared)
        self.assertEqual(len(self.link.request_dolly_zoom.call_args_list),3)
        self.assertEqual(self.link.request_dolly_zoom.call_args.args,(0x92,0x23,0x15,b''))

    def test_land_is_attempted_even_if_stop_fails(self):
        self.link.request_dolly_zoom.side_effect=RuntimeError('no stop')
        self.mission.land()
        self.link.land.assert_called_once()

    def test_preparation_and_start_can_share_a_cancellation_token(self):
        token=threading.Event()
        self.mission.prepare(cancel_event=token)
        self.assertEqual(self.mission.start(confirmed=True,cancel_event=token),b'\0')

    def test_missing_start_ack_preserves_may_fly_state_and_blocks_retry(self):
        self.mission.prepare()
        self.link.request_dolly_zoom.side_effect=None
        self.link.request_dolly_zoom.return_value=None
        self.assertIsNone(self.mission.start(confirmed=True))
        self.assertTrue(self.link.native_task_may_fly)
        with self.assertRaises(RuntimeError):self.mission.start(confirmed=True)

    def test_missing_or_changed_camera_state_blocks_start(self):
        self.mission.prepare()
        state=self.link.status();state['camera']={}
        self.link.status.side_effect=None;self.link.status.return_value=state
        self.link.request_dolly_zoom.reset_mock()
        with self.assertRaises(RuntimeError):self.mission.start(confirmed=True)
        self.link.request_dolly_zoom.assert_not_called()

    def test_active_state_requires_fresh_post_start_end_telemetry(self):
        self.assertFalse(self.mission.is_active_or_unknown())
        self.mission.started_at=time.time()-1
        state=self.link.status()
        self.link.status.side_effect=None;self.link.status.return_value=state
        state['subject_observation']['task'].update(at=time.time(),payload_hex='050d000100')
        self.assertTrue(self.mission.is_active_or_unknown())
        state['subject_observation']['task'].update(at=time.time(),payload_hex='0000000101')
        self.assertFalse(self.mission.is_active_or_unknown())
        state['subject_observation']['task']['at']=time.time()-2
        self.assertTrue(self.mission.is_active_or_unknown())

    def test_protocol_rejects_unobserved_values(self):
        for values in [('半身',4,True),('未知',2,True),('半身',2,1)]:
            with self.assertRaises(ValueError):parameter_writes(*values)
        for request in [(0x17,3,0xf9,bytes.fromhex('878867a305000000')),
                        (0x92,0x23,0x13,b'\x01\x05\x00'),(0x92,0x23,0x15,b'\0')]:
            with self.assertRaises(ValueError):validate_request(*request)
