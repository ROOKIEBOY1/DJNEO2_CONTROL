"""Explicitly confirmed native Circle task using capture-derived commands."""
import threading
import time
import math
from circle_protocol import (CIRCLE_SELECTION_PAYLOAD, CIRCLE_START_PAYLOAD, RADIUS_HASH_BYTES,
                             parse_radius_value, radius_payload, validate_radius)

RADIUS_2M_PAYLOAD = bytes.fromhex('4693270300000040')

class NativeCircle:
    def __init__(self, link):
        self.link = link
        self.prepared = False
        self.radius_m = 2.
        self.started_at = None
        self.last_start_reply = None
        self.last_stop_reply = None
        self.land_requested = False
        self._lock = threading.Lock()
        self._operation = threading.Lock()
        self._cancel = threading.Event()

    def _ensure_link(self):
        status = self.link.status()
        if status.get('land_sent') or status.get('land_requested'):
            raise RuntimeError('本会话已请求降落；确认落地后退出重连，再启动环绕')
        if (not status.get('device_verified') or status.get('transport_mode') != 'reliable'
                or any(age is None or not 0 <= age <= 1 for age in
                       (status.get('last_downlink_age_s'), status.get('last_video_age_s')))):
            raise RuntimeError('设备回复、视频或通信不新鲜，未发送环绕任务')

    def _begin(self, cancel_event=None):
        if not self._operation.acquire(blocking=False):
            raise RuntimeError('已有准备或启动正在处理；可输入停止或降落')
        with self._lock:
            if self._cancel is not cancel_event:
                self._cancel.set()
            self._cancel = cancel_event if cancel_event is not None else threading.Event()
            return self._cancel

    @staticmethod
    def _check_cancel(cancel):
        if cancel.is_set():
            raise RuntimeError('操作已取消，后续命令未发送')

    def _inactive_observed(self):
        task = self.link.status().get('subject_observation', {}).get('task')
        return bool(task and self.started_at is not None and task.get('at', 0) >= self.started_at
                    and task.get('payload_hex') in ('0000000101', '0000000102', '0400000100', '0300000100'))

    def is_active_or_unknown(self):
        return self.started_at is not None and not self._inactive_observed()

    def wait_execution(self, *, timeout=12., cancel_event=None):
        """Observe task progression; the start ACK alone is not execution."""
        deadline = time.monotonic() + timeout
        cancel = cancel_event if cancel_event is not None else self._cancel
        result = dict(executing=False, task=None, candidate_count=None)
        while not cancel.is_set():
            status = self.link.status()
            observation = status.get('subject_observation', {})
            task = observation.get('task')
            if task and self.started_at is not None and task.get('at', 0) >= self.started_at:
                result['task'] = task.get('payload_hex')
                if result['task'] == '0505000100':
                    result['executing'] = True
            result['candidate_count'] = observation.get('candidate_count')
            if result['executing'] or time.monotonic() >= deadline:
                return result
            cancel.wait(min(.1, max(0, deadline - time.monotonic())))
        return dict(result, canceled=True)

    def prepare(self, radius_m=2., *, cancel_event=None):
        radius_m = validate_radius(radius_m)
        cancel = self._begin(cancel_event)
        self.prepared = False
        try:
            self._check_cancel(cancel)
            if self.land_requested:
                raise RuntimeError('已请求降落；请确认落地后退出重连再试')
            if self.started_at is not None and not self._inactive_observed():
                raise RuntimeError('上一任务未确认结束，不能重新设置参数')
            self._ensure_link()
            self.link.center_sticks()
            for receiver, payload in ((0x17, CIRCLE_SELECTION_PAYLOAD), (0x92, radius_payload(radius_m))):
                self._check_cancel(cancel)
                reply = self.link.request_native(receiver, 3, 0xf9, payload, cancel_event=cancel)
                if reply != b'\x00'+payload:
                    raise RuntimeError('模式或半径写入未收到匹配回显；未启动环绕')
            self._check_cancel(cancel)
            reply = self.link.request_native(0x92, 3, 0xf8, RADIUS_HASH_BYTES, cancel_event=cancel)
            if reply is None or not math.isclose(parse_radius_value(reply), radius_m, rel_tol=0, abs_tol=1e-5):
                raise RuntimeError('半径读回与要求不一致；未启动环绕')
            self._check_cancel(cancel)
            self.prepared = True
            self.radius_m = radius_m
            return radius_m
        finally:
            self._operation.release()

    def start(self, *, confirmed=False, cancel_event=None):
        if not confirmed:
            raise ValueError('环绕可能自动起飞，必须明确确认')
        cancel = self._begin(cancel_event)
        try:
            self._check_cancel(cancel)
            if not self.prepared or self.land_requested:
                raise RuntimeError('请先准备并读回半径参数；降落后需要退出重连')
            if self.started_at is not None and not self._inactive_observed():
                raise RuntimeError('上一任务尚未确认结束；先停止并观察飞机')
            self._ensure_link()
            self._check_cancel(cancel)
            self.started_at = time.time()
            self.last_start_reply = None
            # An unanswered start may still initiate takeoff. Preserve the exit
            # landing attempt without pretending a 03/2A takeoff ACK was seen.
            self.link.native_task_may_fly = True
            reply = self.link.request_native(0x92, 0x23, 0x13, CIRCLE_START_PAYLOAD, cancel_event=cancel)
            self.last_start_reply = None if reply is None else reply.hex()
            return reply
        finally:
            self._operation.release()

    def stop(self, timeout=2.):
        with self._lock:
            self._cancel.set()
            self.last_stop_reply = None
        try:
            self.link.center_sticks()
        except (OSError, RuntimeError):
            # Independent task-stop request must still be attempted.
            pass
        reply = self.link.request_native(0x92, 0x23, 0x15, b'', timeout=timeout)
        self.last_stop_reply = None if reply is None else reply.hex()
        return reply

    def land(self, timeout=2.):
        self.land_requested = True
        try:
            self.stop(timeout=.4)
        except (OSError, RuntimeError, TimeoutError):
            pass
        return self.link.land(timeout=timeout)

    def status(self):
        return dict(prepared=self.prepared, prepared_2m=self.prepared and self.radius_m == 2,
                    radius_m=self.radius_m, started_at=self.started_at,
                    start_reply=self.last_start_reply, stop_reply=self.last_stop_reply,
                    land_requested=self.land_requested, aircraft=self.link.status())
