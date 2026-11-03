import threading
import time
from pathlib import Path
import tempfile
import json
from unittest.mock import patch
import unittest
from unittest.mock import Mock
from native_camera import NativeCamera, CameraObservation, validate_camera_request, build_camera_time, PHOTO_CONTEXT_PAYLOAD
from duml import encode_frame


class CameraTests(unittest.TestCase):
    def test_video_preparation_sets_mode_without_starting_recording(self):
        self.link.status.return_value['camera'].update(mode_raw=1,at=time.time()+1)
        self.camera.prepare_video_mode()
        self.assertEqual([c.args[:2] for c in self.link.request_camera.call_args_list],
                         [(0xff,bytes.fromhex('003700020000'))])
        self.assertFalse(self.camera.recording_requested)

    def test_video_preparation_requires_matching_ack(self):
        self.link.request_camera.return_value=b'\xe3'
        with self.assertRaises(RuntimeError):self.camera.prepare_video_mode()

    def setUp(self):
        self.link = Mock()
        self.link.status.return_value = dict(device_verified=True, transport_mode='reliable',
            last_downlink_age_s=.01, camera=dict(age_s=.01,recording_candidate=False,time_synced=True))
        self.link.request_camera.return_value = b'\x00'
        self.link.select_photo_context.return_value=b'\x00'+PHOTO_CONTEXT_PAYLOAD
        self.camera = NativeCamera(self.link)

    def test_photo_and_record_select_the_capture_observed_modes(self):
        self.camera.shoot()
        self.link.select_photo_context.assert_called_once()
        self.assertEqual([(c.args[0],c.args[1]) for c in self.link.request_camera.call_args_list],
                         [(0xff,bytes.fromhex('003700010100')), (1,b'\x01')])
        self.link.request_camera.reset_mock()
        self.camera.start_recording()
        self.camera.stop_recording()
        self.assertEqual([(c.args[0],c.args[1]) for c in self.link.request_camera.call_args_list],
                         [(0xff,bytes.fromhex('003700020000')), (2,b'\x01'), (2,b'\x00')])

    def test_photo_context_must_echo_before_camera_mode_and_shutter(self):
        self.link.select_photo_context.return_value=b'\x00'
        with self.assertRaisesRegex(RuntimeError,'前置'):
            self.camera.shoot()
        self.link.request_camera.assert_not_called()

    def test_photo_context_is_applied_before_camera_mode(self):
        self.camera.shoot()
        calls=[c[0] for c in self.link.method_calls if c[0] in ('select_photo_context','request_camera')]
        self.assertEqual(calls,['select_photo_context','request_camera','request_camera'])

    def test_stop_during_photo_context_prevents_camera_mode_and_shutter(self):
        entered,release=threading.Event(),threading.Event()
        def select(**kwargs):
            entered.set();release.wait(1)
            return b'\x00'+PHOTO_CONTEXT_PAYLOAD
        self.link.select_photo_context.side_effect=select
        errors=[]
        def shoot():
            try:self.camera.shoot()
            except RuntimeError as exc:errors.append(str(exc))
        worker=threading.Thread(target=shoot);worker.start()
        try:
            self.assertTrue(entered.wait(.5));self.camera.stop_recording()
        finally:
            release.set();worker.join(1)
        self.assertEqual([c.args[:2] for c in self.link.request_camera.call_args_list],[(2,b'\x00')])
        self.assertTrue(errors)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_context_is_present_before_photo_mode_in_both_successful_phone_captures(self):
        captures=Path(__file__).resolve().parents[1]/'captures'
        for stamp in ('141959','164926'):
            with self.subTest(capture=stamp):
                with (captures/f'camera-media-20261030-{stamp}.duml.jsonl').open(encoding='utf-8') as source:
                    rows=[json.loads(line) for line in source]
                contexts=[r for r in rows if (r['side'],r['sender'],r['receiver'],r['command_set'],r['command_id'],r['payload'])
                    ==('app',2,0x17,3,0xf9,PHOTO_CONTEXT_PAYLOAD.hex())]
                modes=[r for r in rows if (r['side'],r['receiver'],r['command_set'],r['command_id'],r['payload'])
                    ==('app',1,2,0xff,'003700010100')]
                shutters=[r for r in rows if (r['side'],r['receiver'],r['command_set'],r['command_id'],r['payload'])
                    ==('app',1,2,1,'01')]
                self.assertTrue(contexts and modes and shutters)
                self.assertLess(contexts[0]['at'],modes[0]['at'])
                self.assertLess(modes[0]['at'],shutters[0]['at'])
                context=contexts[0]
                self.assertTrue(any((r['side'],r['sender'],r['receiver'],r['command_set'],r['command_id'],r['sequence'],r['payload'])
                    ==('plane',0x17,2,3,0xf9,context['sequence'],'00'+PHOTO_CONTEXT_PAYLOAD.hex())
                    and r['flags']&0x80 and 0<=r['at']-context['at']<=3 for r in rows))

    def test_bad_mode_reply_blocks_capture_and_recording(self):
        self.link.request_camera.return_value = b'\xe3'
        with self.assertRaises(RuntimeError): self.camera.shoot()
        self.assertEqual(self.link.request_camera.call_count,1)
        self.assertFalse(self.camera.recording_requested)

    def test_stop_during_mode_switch_cancels_later_record_start(self):
        entered, release = threading.Event(), threading.Event()
        def request(command, payload, **kwargs):
            if command == 0xff:
                entered.set(); release.wait(1)
            return b'\x00'
        self.link.request_camera.side_effect = request
        errors=[]
        def start():
            try: self.camera.start_recording()
            except RuntimeError as exc: errors.append(str(exc))
        worker=threading.Thread(target=start);worker.start()
        try:
            self.assertTrue(entered.wait(.5))
            self.camera.stop_recording()
        finally:
            release.set();worker.join(1)
        self.assertFalse(any(c.args[:2] == (2,b'\x01') for c in self.link.request_camera.call_args_list))
        self.assertTrue(errors)

    def test_shoot_does_not_interrupt_an_existing_recording(self):
        self.link.status.return_value['camera'] = dict(age_s=.01, recording_candidate=True)
        with self.assertRaises(RuntimeError): self.camera.shoot()
        self.link.request_camera.assert_not_called()

    def test_missing_or_stale_camera_state_cannot_be_treated_as_idle(self):
        for state in ({},dict(age_s=2,recording_candidate=False),dict(age_s=.01,recording_candidate=None)):
            self.link.status.return_value['camera']=state
            with self.assertRaises(RuntimeError):self.camera.shoot()
        self.link.request_camera.assert_not_called()

    def test_unobserved_requests_cannot_be_sent(self):
        for command,payload in ((1,b'\x00'),(2,b'\x02'),(0xff,b'unknown'),(0xc,bytes.fromhex('01010001'))):
            with self.assertRaises(ValueError):validate_camera_request(command,payload)

    def test_download_latest_uses_live_directory_and_rejects_unknown_size(self):
        item=dict(name='DJI_20261030142114_0034_D.JPG',kind='photo',
                  path='DCIM/DJI_001/DJI_20261030142114_0034_D.JPG',size=5636096)
        self.link.list_media.return_value=[item]
        with tempfile.TemporaryDirectory() as directory,patch('media_transfer.download_directory_item',
                create=True,return_value={'size':5636096,'sha256':'test'}) as download:
            result=self.camera.download_latest('photo',Path(directory))
            self.assertEqual(Path(result['path']).name,item['name'])
            download.assert_called_once()
            self.assertEqual(download.call_args.args[0],item)
            self.assertTrue(download.call_args.kwargs['connect'])
            self.assertIsNotNone(download.call_args.kwargs['cancel_event'])
            item['size']=None
            with self.assertRaises(RuntimeError):self.camera.download_latest('photo',Path(directory))

    def test_recording_must_stop_before_downloading_latest_video(self):
        self.link.status.return_value['camera']['recording_candidate']=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):self.camera.download_latest('video',Path(directory))
        self.link.list_media.assert_not_called()

    def test_camera_state_requires_correct_crc_and_freshness(self):
        observation=CameraObservation()
        payload=bytearray(60);payload[0]=0x85;payload[4]=1
        frame=encode_frame(1,2,1,0,2,0x80,bytes(payload))
        observation.observe(frame,100)
        self.assertTrue(observation.snapshot(100.5)['recording_candidate'])
        self.assertEqual(observation.snapshot(100.5)['mode_raw'],1)
        self.assertIsNone(observation.snapshot(102)['recording_candidate'])
        bad=bytearray(frame);bad[-1]^=1
        observation.observe(bytes(bad),103)
        self.assertEqual(observation.snapshot(100.5)['at'],100)

    def test_time_payload_matches_captured_calendar_and_offset(self):
        import datetime
        now=datetime.datetime(2026,10,30,14,20,47,tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        self.assertEqual(build_camera_time(now).hex(),'ea070a1e0e142fe001')
        validate_camera_request(0x54,build_camera_time(now))

    def test_unsynced_camera_is_initialized_before_mode_switch(self):
        state=self.link.status.return_value['camera'];state['time_synced']=False
        def request(command,payload,**kwargs):
            if command==0x54:state.update(time_synced=True,at=time.time())
            return b'\x00'
        self.link.request_camera.side_effect=request
        self.camera.shoot()
        self.assertEqual([c.args[0] for c in self.link.request_camera.call_args_list],[0x54,0xff,1])

    def test_auto_ended_recording_clears_pending_and_reports_instead_of_false_close_failure(self):
        state=self.link.status.return_value['camera']
        self.camera.recording_requested=True
        self.camera.recording_attempt_at=time.time()-2
        state.update(recording_candidate=True,record_state_raw=2,at=time.time())
        self.camera.poll_recording_status()
        state.update(recording_candidate=False,record_state_raw=0,at=time.time())
        message=self.camera.poll_recording_status()
        self.assertIn('提前结束',message)
        self.assertFalse(self.camera.recording_requested)
        self.camera.close()
        self.link.request_camera.assert_not_called()

    def test_camera_state_exposes_unsynced_flag_and_full_record_state(self):
        observation=CameraObservation()
        payload=bytearray(60);payload[:4]=bytes.fromhex('81028000');payload[4]=1
        observation.observe(encode_frame(1,2,1,0,2,0x80,bytes(payload)))
        state=observation.snapshot()
        self.assertFalse(state['time_synced'])
        self.assertEqual(state['record_state_raw'],2)

    def test_clock_ack_without_synced_telemetry_cannot_enable_capture(self):
        self.link.status.return_value['camera']['time_synced']=False
        with self.assertRaisesRegex(RuntimeError,'尚未回报'):
            self.camera._synchronize_clock(threading.Event(),timeout=.01)
        self.assertEqual([c.args[0] for c in self.link.request_camera.call_args_list],[0x54])

    def test_stop_during_clock_initialization_prevents_mode_and_start(self):
        state=self.link.status.return_value['camera'];state['time_synced']=False
        entered,release=threading.Event(),threading.Event()
        def request(command,payload,**kwargs):
            if command==0x54:entered.set();release.wait(1)
            return b'\x00'
        self.link.request_camera.side_effect=request
        errors=[]
        def start():
            try:self.camera.start_recording()
            except RuntimeError as exc:errors.append(str(exc))
        worker=threading.Thread(target=start);worker.start()
        try:
            self.assertTrue(entered.wait(.5));self.camera.stop_recording()
        finally:
            release.set();worker.join(1)
        self.assertEqual([c.args[0] for c in self.link.request_camera.call_args_list],[0x54,2])
        self.assertTrue(errors)

    def test_recording_started_during_clock_sync_blocks_mode_and_photo(self):
        state=self.link.status.return_value['camera'];state['time_synced']=False
        def request(command,payload,**kwargs):
            if command==0x54:state.update(time_synced=True,recording_candidate=True,at=time.time())
            return b'\x00'
        self.link.request_camera.side_effect=request
        with self.assertRaises(RuntimeError):self.camera.shoot()
        self.assertEqual([c.args[0] for c in self.link.request_camera.call_args_list],[0x54])

    def test_expected_record_stop_is_not_reported_as_premature(self):
        state=self.link.status.return_value['camera']
        self.camera.recording_requested=True
        self.camera.recording_attempt_at=time.time()-2
        state.update(record_state_raw=2,recording_candidate=True,at=time.time())
        self.camera.poll_recording_status()
        self.camera._recording_stop_requested=True
        state.update(record_state_raw=0,recording_candidate=False,at=time.time())
        message=self.camera.poll_recording_status()
        self.assertNotIn('提前',message)
