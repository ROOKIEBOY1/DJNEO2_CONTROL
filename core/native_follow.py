"""One ground-start Follow trial per connection; no speculative parameters."""
import threading
import time
from follow_protocol import SELECTION_PAYLOAD, START_PAYLOAD


class NativeFollow:
    def __init__(self, link):
        self.link = link
        self._operation = threading.Lock()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.prepared = False
        self.started_at = None
        self.land_requested = False
        self.last_start_reply = self.last_stop_reply = None

    @staticmethod
    def _check(token):
        if token.is_set():
            raise RuntimeError('跟随操作已取消，后续指令未发送')

    def _begin(self, cancel_event):
        if not self._operation.acquire(False):
            raise RuntimeError('上一操作正在处理；可输入停止或降落')
        with self._lock:
            if self._cancel is not cancel_event:
                self._cancel.set()
            self._cancel = cancel_event if cancel_event is not None else threading.Event()
            return self._cancel

    def _ensure_ground(self):
        state = self.link.status()
        if (not state.get('device_verified') or state.get('transport_mode') != 'reliable'
                or any(state.get(k) is None or not 0 <= state[k] <= 1
                       for k in ('last_downlink_age_s', 'last_video_age_s'))):
            raise RuntimeError('设备、通信或视频不新鲜，未发送跟随')
        if self.land_requested or state.get('land_requested') or state.get('land_sent'):
            raise RuntimeError('本轮已请求降落；确认落地后退出重连')
        flight = state.get('landing_observation', {})
        age = flight.get('telemetry_age_s')
        if (age is None or not 0 <= age <= 1 or flight.get('motors_on') is not False
                or flight.get('airborne_flag') is not False):
            raise RuntimeError('本入口只从地面启动，需新鲜的电机停止及非空中状态')
        task = state.get('subject_observation', {}).get('task')
        if not (task and 0 <= time.time()-task.get('at', 0) <= 1 and task.get('phase_raw') == 0):
            raise RuntimeError('原生任务尚未确认空闲，未启动跟随')

    def prepare(self, *, cancel_event=None):
        token = self._begin(cancel_event)
        self.prepared = False
        try:
            self._check(token)
            if self.started_at is not None:
                raise RuntimeError('本连接已启动过任务；落地后退出重连')
            self._ensure_ground()
            self.link.center_sticks()
            self._check(token)
            reply = self.link.request_follow(0x17, 3, 0xf9, SELECTION_PAYLOAD, cancel_event=token)
            if reply != b'\0'+SELECTION_PAYLOAD:
                raise RuntimeError('跟随模式未收到完整匹配回显，未启动')
            self._ensure_ground()
            with self._lock:
                self._check(token)
                self.prepared = True
        finally:
            self._operation.release()

    def start(self, *, confirmed=False, cancel_event=None):
        if not confirmed:
            raise ValueError('跟随可能自动起飞，需要明确确认')
        token = self._begin(cancel_event)
        try:
            self._check(token)
            if not self.prepared or self.started_at is not None:
                raise RuntimeError('请先准备；每次连接只启动一轮跟随')
            self._ensure_ground()
            with self._lock:
                self._check(token)
                self.prepared = False
                self.started_at = time.time()
                self.link.native_task_may_fly = True
            reply = self.link.request_follow(0x92, 0x23, 0x13, START_PAYLOAD, cancel_event=token)
            self.last_start_reply = None if reply is None else reply.hex()
            return reply
        finally:
            self._operation.release()

    def stop(self, timeout=2.):
        with self._lock:
            self._cancel.set()
            self.prepared = False
        try:
            self.link.center_sticks()
        except (OSError, RuntimeError):
            pass
        reply = self.link.request_follow(0x92, 0x23, 0x15, b'', timeout=timeout)
        self.last_stop_reply = None if reply is None else reply.hex()
        return reply

    def is_active_or_unknown(self):
        if self.started_at is None:
            return False
        task = self.link.status().get('subject_observation', {}).get('task')
        return not bool(task and task.get('at', 0) > self.started_at
                        and 0 <= time.time()-task.get('at', 0) <= 1
                        and task.get('payload_hex') in ('0000000101', '0000000102'))

    def land(self, timeout=2.):
        self.land_requested = True
        with self._lock:
            self._cancel.set()
            self.prepared = False
        if self.started_at is None or self.is_active_or_unknown():
            try:
                self.stop(timeout=.4)
            except (OSError, RuntimeError, TimeoutError):
                pass
        return self.link.land(timeout=timeout)

    def status(self):
        state = self.link.status()
        task = state.get('subject_observation', {}).get('task')
        executing = bool(task and self.started_at is not None and task.get('at', 0) >= self.started_at
                         and 0 <= time.time()-task.get('at', 0) <= 1
                         and task.get('payload_hex') == '050a000100')
        return dict(prepared=self.prepared, started_at=self.started_at,
                    executing_observed=executing, last_start_reply=self.last_start_reply,
                    last_stop_reply=self.last_stop_reply, land_requested=self.land_requested,
                    aircraft=state)
