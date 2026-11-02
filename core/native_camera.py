"""Neo 2 camera requests from the user's 2026-10-30 labeled capture."""
from collections import deque
from pathlib import Path
import threading
import time
import datetime
import struct
from control_packets import valid_frames

PHOTO_MODE = bytes.fromhex('003700010100')
VIDEO_MODE = bytes.fromhex('003700020000')
PHOTO_CONTEXT_PAYLOAD=bytes.fromhex('878867a309000000')


def build_camera_time(now=None):
    zone=datetime.timezone(datetime.timedelta(hours=8))
    now=datetime.datetime.now(zone) if now is None else now
    if now.utcoffset() is None:raise ValueError('相机时间需要明确时区')
    return struct.pack('<H5Bh',now.year,now.month,now.day,now.hour,now.minute,now.second,
                       int(now.utcoffset().total_seconds()/60))


def validate_camera_request(command, payload):
    if command==0x54:
        if len(payload)!=9:raise ValueError('相机校时负载长度不匹配')
        year,month,day,hour,minute,second,offset=struct.unpack('<H5Bh',payload)
        datetime.datetime(year,month,day,hour,minute,second)
        if not 2000<=year<=2099 or not -720<=offset<=840:raise ValueError('相机日期或时区超出范围')
        return
    allowed = {1:(b'\x01',), 2:(b'\x00',b'\x01'), 0xff:(PHOTO_MODE,VIDEO_MODE)}
    if command not in allowed or payload not in allowed[command]:
        raise ValueError('只允许本机抓包中已观察到的相机模式、拍照和录像指令')


class CameraObservation:
    def __init__(self):
        self.latest = None
        self.history = deque(maxlen=1024)
        self._saved_at=0

    def observe(self, frame, at=None):
        if (len(frame) != 73 or frame[4:6] != b'\x01\x02' or frame[8:11] != b'\x00\x02\x80'
                or next(valid_frames(frame),None) != frame):
            return
        at = time.time() if at is None else at
        payload=frame[11:-2]
        flags=int.from_bytes(payload[:4],'little')
        value = dict(at=at,mode_raw=payload[4],recording_candidate=bool(flags&0xc0),
                     record_state_raw=(flags>>6)&3,time_synced=bool(flags&4),
                     state_flags_raw=payload[:4].hex(),payload_hex=payload.hex())
        prior = self.latest
        self.latest = value
        if (prior is None or value['mode_raw'] != prior['mode_raw']
                or value['recording_candidate'] != prior['recording_candidate']
                or value['time_synced']!=prior['time_synced']
                or value['record_state_raw']!=prior['record_state_raw'] or at-self._saved_at >= 1):
            self.history.append(value)
            self._saved_at=at

    def snapshot(self, now=None):
        now=time.time() if now is None else now
        age=None if self.latest is None else now-self.latest['at']
        fresh=age is not None and 0<=age<=1
        return dict(at=None if self.latest is None else self.latest['at'],
                    age_s=age, mode_raw=self.latest['mode_raw'] if fresh else None,
                    recording_candidate=self.latest['recording_candidate'] if fresh else None,
                    time_synced=self.latest['time_synced'] if fresh else None,
                    record_state_raw=self.latest['record_state_raw'] if fresh else None,
                    interpretation='本机样本的录像候选标志；不是文件已保存或下载完成的证明')


class NativeCamera:
    def __init__(self, link):
        self.link=link
        self._operation=threading.Lock()
        self._lock=threading.Lock()
        self._cancel=threading.Event()
        self.recording_requested=False
        self.recording_attempt_at=None
        self._recording_seen_at=None
        self._recording_warning=False
        self._recording_stop_requested=False

    def _ensure(self):
        status=self.link.status()
        age=status.get('last_downlink_age_s')
        if (not status.get('device_verified') or status.get('transport_mode')!='reliable'
                or age is None or not 0<=age<=1):
            raise RuntimeError('相机控制需要已核验且新鲜的可靠连接')
        return status.get('camera',{})

    def _begin(self,cancel_event):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('已有相机操作正在处理；可说停止录像')
        with self._lock:
            self._cancel=cancel_event if cancel_event is not None else threading.Event()
            return self._cancel

    @staticmethod
    def _check(token):
        if token.is_set():raise RuntimeError('相机操作已取消')

    def _synchronize_clock(self,token,timeout=2.):
        self._check(token)
        sent_at=time.time()
        reply=self.link.request_camera(0x54,build_camera_time(),cancel_event=token)
        if reply!=b'\x00':
            raise RuntimeError('相机校时未成功：'+('未收到回复' if reply is None else reply.hex()))
        deadline=time.monotonic()+timeout
        while True:
            self._check(token)
            state=self.link.status().get('camera',{})
            age=state.get('age_s')
            if (age is not None and 0<=age<=1 and state.get('time_synced') is True
                    and state.get('at',0)>=sent_at):return
            if time.monotonic()>=deadline:
                raise RuntimeError('相机校时已回复，但尚未回报已同步状态；未继续拍摄')
            token.wait(.05)

    def poll_recording_status(self):
        """Feedback only. Never restart recording or infer file completeness."""
        state=self.link.status().get('camera',{})
        with self._lock:
            if not self.recording_requested or self.recording_attempt_at is None:return None
            age=state.get('age_s')
            if age is None or not 0<=age<=1 or state.get('at',0)<self.recording_attempt_at:return None
            record=state.get('record_state_raw')
            if record==2:
                if self._recording_seen_at is None:
                    self._recording_seen_at=state['at']
                    return '相机已回报录像中；继续监测，回复 00 本身不代表录像持续或已保存。'
            elif record==0 and self._recording_seen_at is not None:
                self.recording_requested=False
                if self._recording_stop_requested:
                    return '相机已回报录像停止；请用“相册”核对文件并下载原片。'
                return '录像已提前结束：相机已回到空闲状态；请用“相册”核对文件，再“下载最新录像”。'
            elif record==0 and time.time()-self.recording_attempt_at>=3 and not self._recording_warning:
                self._recording_warning=True
                return '录像未获得持续状态确认：相机仍为空闲；请停止录像并检查相册，不能只依据 00 判断成功。'
        return None

    def _require_idle(self,state):
        if self.recording_requested or state.get('recording_candidate') is True:
            raise RuntimeError('相机已在录像或录像状态待核实；先停止录像')
        age=state.get('age_s')
        if age is None or not 0<=age<=1 or state.get('recording_candidate') is not False:
            raise RuntimeError('相机状态缺失或过期，尚不能确认未在录像；请等待状态更新')

    def _capture(self,recording,*,cancel_event=None):
        token=self._begin(cancel_event)
        try:
            self._check(token)
            state=self._ensure()
            self._require_idle(state)
            if state.get('time_synced') is not True:
                self._synchronize_clock(token)
                self._require_idle(self._ensure())
            if not recording:
                self._check(token)
                reply=self.link.select_photo_context(cancel_event=token)
                if reply!=b'\x00'+PHOTO_CONTEXT_PAYLOAD:
                    raise RuntimeError('拍照前置参数未收到匹配回显；未切换相机或拍摄')
                self._require_idle(self._ensure())
            mode=VIDEO_MODE if recording else PHOTO_MODE
            self._check(token)
            reply=self.link.request_camera(0xff,mode,cancel_event=token)
            if reply!=b'\x00':
                raise RuntimeError('相机模式未成功：'+('未收到回复' if reply is None else reply.hex())+'；未继续拍摄')
            self._require_idle(self._ensure())
            with self._lock:
                self._check(token)
                if recording:
                    # Missing ACK does not prove that recording did not start.
                    self.recording_requested=True
                    self.recording_attempt_at=time.time()
                    self._recording_seen_at=None
                    self._recording_warning=False
                    self._recording_stop_requested=False
            return self.link.request_camera(2 if recording else 1,b'\x01',cancel_event=token)
        finally:
            self._operation.release()

    def shoot(self,*,cancel_event=None):
        return self._capture(False,cancel_event=cancel_event)

    def start_recording(self,*,cancel_event=None):
        return self._capture(True,cancel_event=cancel_event)

    def stop_recording(self,timeout=2.):
        with self._lock:
            self._cancel.set()
            self._recording_stop_requested=True
        self._ensure()
        reply=self.link.request_camera(2,b'\x00',timeout=timeout)
        if reply==b'\x00':self.recording_requested=False
        return reply

    def list_media(self,*,cancel_event=None):
        token=self._begin(cancel_event)
        try:
            self._check(token)
            self._ensure()
            return self.link.list_media(cancel_event=token)
        finally:
            self._operation.release()

    def download_latest(self,kind,output_directory,*,cancel_event=None):
        self.poll_recording_status()
        if kind not in ('photo','video'):raise ValueError('请选择照片或录像')
        token=self._begin(cancel_event)
        try:
            self._check(token)
            state=self._ensure()
            if kind=='video' and (self.recording_requested or state.get('recording_candidate') is not False):
                raise RuntimeError('请先停止录像并等待相机状态更新，再下载最新录像')
            entries=self.link.list_media(cancel_event=token)
            candidates=[entry for entry in entries if entry.get('kind')==kind]
            if not candidates:raise RuntimeError('相册中没有对应的原片')
            entry=candidates[0]
            name=entry['name']
            if Path(name).name!=name or not isinstance(entry.get('size'),int) or entry['size']<=0:
                raise RuntimeError('最新文件的名称或大小尚未可靠解析，未下载旧文件替代')
            self._check(token)
            destination=Path(output_directory).resolve()/name
            from media_transfer import download_directory_item
            result=download_directory_item(entry,destination,connect=True,cancel_event=token)
            return dict(result,path=str(destination))
        finally:
            self._operation.release()

    def close(self):
        self.poll_recording_status()
        with self._lock:
            self._cancel.set()
        if self.recording_requested:
            reply=self.stop_recording(timeout=.5)
            if reply!=b'\x00':raise RuntimeError('退出时录像停止未确认，请检查飞机相机状态')
