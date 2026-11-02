from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock
from voice_commands import parse_voice_command
from voice_control import VoiceExecutor
from voice_motion import CalibrationStore
from test_voice_control import FakeController


class VoiceCameraTests(unittest.TestCase):
    def test_recording_state_feedback_is_delivered_to_the_voice_log(self):
        self.camera.poll_recording_status.return_value='录像已提前结束'
        self.executor.poll_camera()
        self.assertTrue(any(e.get('result')=='录像已提前结束' for e in self.executor.events))
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.camera=Mock()
        self.camera.shoot.return_value=b'\x00'
        self.camera.start_recording.return_value=b'\x00'
        self.camera.stop_recording.return_value=b'\x00'
        self.link=FakeController()
        self.executor=VoiceExecutor(self.link,CalibrationStore(Path(self.directory.name)/'c.json'),
            notify=lambda _:None,camera=self.camera)
    def tearDown(self):
        self.executor.close()
        self.directory.cleanup()
    def idle(self):
        until=time.monotonic()+1
        while self.executor.busy and time.monotonic()<until:time.sleep(.005)
        self.assertFalse(self.executor.busy)
    def test_camera_intents_never_fall_through_to_takeoff(self):
        for text,kind in [('拍照','camera_photo'),('开始录像','camera_record_start'),('停止录像','camera_record_stop')]:
            self.assertEqual(parse_voice_command(text).kind,kind)
        self.executor.handle('拍照')
        self.camera.shoot.assert_not_called()
        self.executor.handle('启用语音',manual=True)
        self.executor.handle('拍照');self.idle()
        self.executor.handle('开始录像');self.idle()
        self.executor.handle('停止录像');self.idle()
        self.camera.shoot.assert_called_once()
        self.camera.start_recording.assert_called_once()
        self.camera.stop_recording.assert_called_once()
        self.assertEqual(self.link.takeoffs,0)
    def test_explicit_recording_stop_is_available_from_keyboard_when_voice_paused(self):
        self.executor.handle('停止录像',manual=True)
        self.camera.stop_recording.assert_called_once()

    def test_stop_before_native_camera_worker_entry_prevents_late_record_start(self):
        from native_camera import NativeCamera
        entered,release=threading.Event(),threading.Event()
        class GatedCamera(NativeCamera):
            def _begin(self,cancel_event):
                entered.set();release.wait(1)
                return super()._begin(cancel_event)
        self.link.status=lambda:dict(device_verified=True,transport_mode='reliable',
            last_downlink_age_s=.01,camera=dict(age_s=.01,recording_candidate=False))
        self.link.request_camera=Mock(return_value=b'\x00')
        self.executor.camera=GatedCamera(self.link)
        self.executor.enabled=True
        self.executor.handle('开始录像')
        try:
            self.assertTrue(entered.wait(.5))
            self.executor.handle('停止录像')
        finally:
            release.set();self.idle()
        self.assertEqual([c.args[:2] for c in self.link.request_camera.call_args_list],[(2,b'\x00')])

    def test_stop_recording_does_not_cancel_an_unrelated_direction_action(self):
        self.link.block=True
        self.executor.enabled=True
        self.executor._camera_cancel=threading.Event()
        self.executor.handle('前进一点')
        until=time.monotonic()+.5
        while not self.link.moves and time.monotonic()<until:time.sleep(.005)
        self.executor.handle('停止录像')
        self.assertFalse(self.executor._cancel.is_set())
        self.assertTrue(self.executor._camera_cancel.is_set())
        self.camera.stop_recording.assert_called_once()

    def test_live_album_and_download_commands_are_dispatched_without_flight(self):
        self.executor.enabled=True
        self.camera.list_media.return_value=[dict(name='photo.JPG',size=10)]
        self.camera.download_latest.return_value={'path':'photo.JPG','size':10}
        self.executor.handle('相册');self.idle()
        self.executor.handle('下载最新照片');self.idle()
        self.executor.handle('下载最新录像');self.idle()
        self.camera.list_media.assert_called_once()
        self.assertEqual([c.args[0] for c in self.camera.download_latest.call_args_list],['photo','video'])
        self.assertEqual(self.link.takeoffs,0)
