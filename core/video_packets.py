"""Receive-only packet assembly; no decoder or UI."""
from collections import OrderedDict
import time
from outer import parse_outer

class VideoAssembler:
    """Join complete observed channel-2 frames; never decode partial frames.

    Prefix: frame id, count[6:0]/part[0], flags[7:5]/part[5:1], metadata.
    Upper flags are ignored, not treated as FEC. No loss recovery is attempted.
    """
    def __init__(self, max_pending=32, max_age=1.0):
        self.max_pending = max_pending
        self.max_age = max_age
        self._pending = OrderedDict()
        self._completed = OrderedDict()
        self.dropped_frames = 0
        self.invalid_packets = 0
        self.duplicate_packets = 0

    @property
    def pending_count(self):
        return len(self._pending)

    def feed(self, raw):
        try:
            p = parse_outer(raw)
            if p.channel != 2 or len(raw) <= 20:
                raise ValueError('not video')
            frame, count = raw[16], raw[17] & 0x7f
            part = ((raw[18] & 0x1f) << 1) | (raw[17] >> 7)
            if not 1 <= count <= 64 or part >= count:
                raise ValueError('invalid fragment')
        except (ValueError, IndexError):
            self.invalid_packets += 1
            return None
        now = time.monotonic()
        for key, (created, _) in list(self._pending.items()):
            if now - created > self.max_age:
                del self._pending[key]
                self.dropped_frames += 1
        for key, created in list(self._completed.items()):
            if now - created > self.max_age:
                del self._completed[key]
        key = (p.session, frame, count)
        if key in self._completed:
            self.duplicate_packets += 1
            return None
        if key not in self._pending:
            self._pending[key] = (now, {})
        parts = self._pending[key][1]
        data = bytes(raw[20:])
        if part in parts:
            self.duplicate_packets += 1
            # Conflicting duplicates make the frame unsafe to decode.
            if parts[part] != data:
                del self._pending[key]
                self._completed[key] = now
                self.dropped_frames += 1
            return None
        parts[part] = data
        if len(parts) == count:
            del self._pending[key]
            self._completed[key] = now
            # Bound even if an adversarial source invents many sessions.
            while len(self._completed) > 512:
                self._completed.popitem(last=False)
            return b''.join(parts[i] for i in range(count))
        while len(self._pending) > self.max_pending:
            self._pending.popitem(last=False)
            self.dropped_frames += 1
        return None
