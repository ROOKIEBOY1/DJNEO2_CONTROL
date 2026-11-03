"""Ground-start native dolly zoom; capture-derived profile, explicit start only."""
import threading
import time
from dolly_zoom_protocol import START_PAYLOAD, selection_payload, parameter_writes
from native_camera import NativeCamera

class NativeDollyZoom:
    def __init__(self,link,camera=None):
        self.link=link
        self.camera=camera if camera is not None else NativeCamera(link)
        self._operation=threading.Lock()
        self._lock=threading.Lock()
        self._cancel=threading.Event()
        self.prepared=False
        self.config=None
        self.started_at=None
        self.land_requested=False
        self.last_start_reply=None
        self.last_stop_reply=None

    @staticmethod
    def _check(token):
        if token.is_set():raise RuntimeError('滑动变焦操作已取消，后续指令未发送')

    def _begin(self,cancel_event):
        if not self._operation.acquire(False):raise RuntimeError('正在处理上一操作；可输入停止或降落')
        with self._lock:
            if self._cancel is not cancel_event:self._cancel.set()
            self._cancel=cancel_event if cancel_event is not None else threading.Event()
            return self._cancel

    def _ensure_ground(self):
        state=self.link.status()
        if (not state.get('device_verified') or state.get('transport_mode')!='reliable'
            or any(state.get(k) is None or not 0<=state[k]<=1
                   for k in ('last_downlink_age_s','last_video_age_s'))):
            raise RuntimeError('设备、通信或视频状态未确认，未发送滑动变焦')
        if self.land_requested or state.get('land_requested') or state.get('land_sent'):
            raise RuntimeError('本轮已请求降落；落地后退出重连再试')
        flight=state.get('landing_observation',{})
        age=flight.get('telemetry_age_s')
        task=state.get('subject_observation',{}).get('task')
        if (age is None or not 0<=age<=1 or flight.get('motors_on') is not False
                or flight.get('airborne_flag') is not False):
            raise RuntimeError('本入口仅从地面启动，需新鲜的电机停止及非空中状态')
        if (not task or not 0<=time.time()-task.get('at',0)<=1 or task.get('phase_raw')!=0):
            raise RuntimeError('原生任务尚未确认空闲，未准备或启动滑动变焦')
        return state

    def prepare(self,framing='半身',zoom=2,retreat=True,*,cancel_event=None):
        writes=parameter_writes(framing,zoom,retreat)
        token=self._begin(cancel_event)
        self.prepared=False
        try:
            self._check(token)
            if self.started_at is not None:raise RuntimeError('本轮已发起任务；落地后退出重连再试')
            self._ensure_ground()
            self.link.center_sticks()
            # Fly clears the prior native context before selecting mode 13.
            self._check(token)
            if self.link.request_dolly_zoom(0x92,0x23,0x15,b'',cancel_event=token)!=b'\0':
                raise RuntimeError('旧任务清理未确认，未切入滑动变焦模式')
            requests=[(0x17,selection_payload())]+[(0x92,p) for p in writes]
            for receiver,payload in requests:
                self._check(token);self._ensure_ground()
                reply=self.link.request_dolly_zoom(receiver,3,0xf9,payload,cancel_event=token)
                if reply!=b'\0'+payload:raise RuntimeError('滑动变焦参数未收到匹配回显；未启动')
            self._check(token)
            self.camera.prepare_video_mode(cancel_event=token)
            self._ensure_ground()
            with self._lock:
                self._check(token)
                self.config=dict(framing=framing,zoom=zoom,retreat=retreat,camera_mode='录像')
                self.prepared=True
            return self.config.copy()
        finally:self._operation.release()

    def start(self,*,confirmed=False,cancel_event=None):
        if not confirmed:raise ValueError('滑动变焦可能自动起飞，需要输入确认')
        token=self._begin(cancel_event)
        try:
            self._check(token)
            if not self.prepared or self.started_at is not None:raise RuntimeError('请先准备；本轮任务不可重复启动')
            self._ensure_ground()
            camera=self.link.status().get('camera',{})
            # Real camera preparation waits for mode telemetry; recheck it before takeoff.
            if (camera.get('age_s') is None or not 0<=camera['age_s']<=1
                    or camera.get('mode_raw')!=1 or camera.get('time_synced') is not True
                    or camera.get('recording_candidate') is not False):
                raise RuntimeError('相机录像模式或空闲状态已变化；未启动任务')
            with self._lock:
                self._check(token)
                self.prepared=False
                self.started_at=time.time()
                self.link.native_task_may_fly=True
            reply=self.link.request_dolly_zoom(0x92,0x23,0x13,START_PAYLOAD,cancel_event=token)
            self.last_start_reply=None if reply is None else reply.hex()
            return reply
        finally:self._operation.release()

    def stop(self,timeout=2.):
        with self._lock:
            self._cancel.set()
            self.prepared=False
        try:self.link.center_sticks()
        except (OSError,RuntimeError):pass
        reply=self.link.request_dolly_zoom(0x92,0x23,0x15,b'',timeout=timeout)
        self.last_stop_reply=None if reply is None else reply.hex()
        return reply

    def land(self,timeout=2.):
        self.land_requested=True
        with self._lock:
            self._cancel.set()
            self.prepared=False
        if self.started_at is None or self.is_active_or_unknown():
            try:self.stop(timeout=.4)
            except (OSError,RuntimeError,TimeoutError):pass
        return self.link.land(timeout=timeout)

    def is_active_or_unknown(self):
        if self.started_at is None:return False
        state=self.link.status()
        task=state.get('subject_observation',{}).get('task')
        return not bool(task and task.get('at',0)>=self.started_at
                        and 0<=time.time()-task.get('at',0)<=1
                        and task.get('payload_hex') in ('0000000101','0000000102','0400000100','0300000100'))

    def status(self):
        state=self.link.status()
        task=state.get('subject_observation',{}).get('task')
        executing=bool(task and self.started_at is not None and task.get('at',0)>=self.started_at
                       and 0<=time.time()-task.get('at',0)<=1 and task.get('payload_hex')=='050d000100')
        return dict(prepared=self.prepared,config=self.config,started_at=self.started_at,
                    executing_observed=executing,last_start_reply=self.last_start_reply,
                    last_stop_reply=self.last_stop_reply,land_requested=self.land_requested,aircraft=state)
