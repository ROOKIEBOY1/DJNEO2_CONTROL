"""Read-only observations of capture-derived Neo 2 vision and task messages.

Detection boxes are NOT a verified subject-lock or permission to start a task.
Only zero/one-target layouts observed in owned captures are accepted. The
coordinate origin and class/state enums remain unverified.
"""
from collections import deque
import copy
import math
import struct
import time
from control_packets import valid_frames

def _bbox(values):
    x, y, width, height = values
    if not all(math.isfinite(v) for v in values) or not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
        raise ValueError('Unsupported normalized box coordinates')
    return dict(x=x, y=y, width=width, height=height)

def parse_detection(payload):
    if len(payload) < 12 or payload[0] != 2:
        raise ValueError('Unsupported 0A/BC detection header')
    count = payload[11]
    if count not in (0, 1) or len(payload) != 12 + 12*count:
        raise ValueError('Only captured zero/one-target detection layouts are supported')
    targets = []
    if count:
        target_id, class_raw, state_raw, x, y, width, height = struct.unpack_from('<HBB4H', payload, 12)
        targets.append(dict(id=target_id, class_raw=class_raw, state_raw=state_raw,
                            bbox=_bbox(tuple(v/10000 for v in (x, y, width, height)))))
    return {'count': count, 'device_counter_raw': int.from_bytes(payload[1:5], 'little'),
            'header_state_raw': payload[5:11].hex(), 'targets': targets}

def parse_tracking_box(payload):
    if payload == b'\x00':
        return {'targets': [], 'marker_raw': 0}
    if len(payload) != 20 or payload[0] != 1:
        raise ValueError('Unsupported 0A/95 box layout')
    return {'marker_raw': payload[0], 'tail_raw': payload[19],
            'targets': [dict(id=payload[1], state_raw=payload[2],
                             bbox=_bbox(struct.unpack_from('<4f', payload, 3)))]}

def parse_task_state(payload):
    if len(payload) != 5:
        raise ValueError('Unsupported 23/14 task-state length')
    return {'phase_raw': payload[0], 'mode_raw': payload[1], 'reserved_raw': payload[2],
            'tail_raw': payload[3:].hex(), 'payload_hex': payload.hex()}

class SubjectObservation:
    """Caller serializes observe/snapshot, as FlightController does."""
    def __init__(self):
        self.latest = {}
        self.errors = {}
        self.unsupported_frames = 0
        self.history = deque(maxlen=4096)
        self._saved = {}

    def observe(self, frame, at=None):
        if len(frame) < 13 or frame[4:6] != b'\x92\x02' or frame[8] != 0:
            return
        parsers = {(10, 0xbc): ('detection', parse_detection),
                   (10, 0x95): ('tracking_box_candidate', parse_tracking_box),
                   (0x23, 0x14): ('task', parse_task_state)}
        entry = parsers.get((frame[9], frame[10]))
        if entry is None or next(valid_frames(frame), None) != frame:
            return
        kind, parser = entry
        at = time.time() if at is None else at
        try:
            value = parser(frame[11:-2])
        except ValueError as exc:
            self.unsupported_frames += 1
            self.latest.pop(kind, None)
            self.errors[kind] = str(exc)
            return
        self.errors.pop(kind, None)
        self.latest[kind] = dict(value, at=at)
        key = (tuple((t['id'], t.get('class_raw'), t.get('state_raw')) for t in value.get('targets', [])),
               value.get('payload_hex'))
        previous = self._saved.get(kind)
        if previous is None or previous[0] != key or at-previous[1] >= 1:
            self.history.append(dict(value, at=at, type=kind))
            self._saved[kind] = key, at

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        def fresh(kind):
            value = self.latest.get(kind)
            age = None if value is None else max(0, now-value['at'])
            return (None if age is None or age > 1 else value), age
        detection, detection_age = fresh('detection')
        tracking, tracking_age = fresh('tracking_box_candidate')
        task, task_age = fresh('task')
        return copy.deepcopy({
            'candidate_count': None if detection is None else detection['count'],
            'candidates': [] if detection is None else detection['targets'],
            'detection_age_s': None if detection_age is None else round(detection_age, 3),
            'tracking_box_candidate': tracking,
            'tracking_box_age_s': None if tracking_age is None else round(tracking_age, 3),
            'task': task, 'task_age_s': None if task_age is None else round(task_age, 3),
            'lock_verified': False,
            'interpretation': ('当前主体框数据缺失、过期或格式未支持。' if detection is None else
                               '当前未检测到候选主体框。' if detection['count'] == 0 else
                               '当前收到候选主体框；类别和状态值仍待验证。') + ' 不作为主体锁定或启动许可。',
            'unsupported_frames': self.unsupported_frames, 'format_errors': dict(self.errors)})
