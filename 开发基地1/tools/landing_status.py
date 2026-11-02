"""Observe Neo 2 OSD fields; never treat a command ACK as touchdown.

Only mode/command/motor bits are used for feedback. Offsets match this project's
own DJI Fly landing capture and 2026-10-29 control capture. No automatic action
or flight permission is derived from these observations.
Reference: o-gs/dji-firmware-tools comm_dissector/wireshark/dji-dumlv1-flyc.lua.
"""
from collections import deque
import time
from control_packets import valid_frames

FEEDBACK = {
    'descending': '飞机回报降落模式；尚未确认落地，请继续观察。',
    'interrupted': '降落已中断：飞机退出降落模式，仍回报电机运转。不要把 00 回复当作落地；请用原厂控制方式接管落地。',
    'not_observed': '降落已发送，但尚未观察到降落模式；未确认落地。',
    'telemetry_stale': '降落状态数据缺失或过期，无法确认是否落地。',
    'motors_off': '飞机回报电机已停止且无空中标志；请目视确认落地。',
}

class LandingStatus:
    def __init__(self):
        self.requested_at = None
        self.phase = 'idle'
        self.latest = None
        self._seen_descending = False
        self._hover_since = None
        self.history = deque(maxlen=4096)
        self._last_saved = None

    def begin(self, at=None):
        self.requested_at = time.time() if at is None else at
        self.phase = 'requested'
        self._seen_descending = False
        self._hover_since = None
        self.history.append({'at': self.requested_at, 'event': 'landing_requested'})

    def new_flight(self):
        self.requested_at = None
        self.phase = 'idle'
        self._seen_descending = False
        self._hover_since = None
        self.history.append({'at': time.time(), 'event': 'new_flight_requested'})

    def observe(self, frame, at=None):
        if (len(frame) < 51 or frame[4] != 3 or frame[9:11] != b'\x03\x43'
                or next(valid_frames(frame), None) != frame):
            return
        at = time.time() if at is None else at
        payload = frame[11:-2]
        flags = int.from_bytes(payload[32:36], 'little')
        self.latest = {'at': at, 'mode_raw': payload[30] & 0x7f,
                       'last_command_raw': payload[31], 'flags_raw': flags,
                       'motors_on': bool(flags & 8), 'airborne_flag': bool(flags & 4),
                       'relative_height_candidate_m': int.from_bytes(payload[16:18], 'little', signed=True)/10}
        if self.requested_at is not None and at >= self.requested_at:
            if self.phase == 'motors_off' and flags & 12:
                self.phase = 'requested'
            if not flags & 12:
                self.phase = 'motors_off'
                self._hover_since = None
            elif self.latest['mode_raw'] == 12:
                self._seen_descending = True
                self._hover_since = None
                self.phase = 'descending'
            elif self._seen_descending and self.latest['mode_raw'] == 6 and flags & 8:
                if self._hover_since is None:
                    self._hover_since = at
                if at - self._hover_since >= .5:
                    self.phase = 'interrupted'
            else:
                self._hover_since = None
        key = (self.latest['mode_raw'], self.latest['last_command_raw'], flags & 15, self.phase)
        if self._last_saved is None or key != self._last_saved[0] or at - self._last_saved[1] >= 1:
            self.history.append(dict(self.latest, phase=self.phase))
            self._last_saved = key, at

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        result = dict(self.latest or {})
        age = None if self.latest is None else max(0, now-self.latest['at'])
        phase = self.phase
        if self.requested_at is not None and now-self.requested_at >= 3:
            if age is None or age > 1:
                phase = 'telemetry_stale'
            elif phase == 'requested':
                phase = 'not_observed'
        return dict(result, phase=phase, requested_at=self.requested_at,
                    telemetry_age_s=None if age is None else round(age, 2))
