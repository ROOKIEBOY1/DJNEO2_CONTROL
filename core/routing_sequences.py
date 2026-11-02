"""Preserve the application's full routing counter across controller restarts."""

import json
import os
from pathlib import Path
import threading

from control_packets import OBSERVED_TRAILER_PREFIX, valid_frames
from duml import crc


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / '.state' / 'routing-sequence.json'


def resequence_tunnels(payload, next_counter):
    frames = list(valid_frames(payload))
    if b''.join(frames) != payload:
        raise ValueError('Routing requires complete validated DUML frames')
    result = []
    for frame in frames:
        if frame[4:6] == b'\x3b\xe9' and frame[9:11] == b'\x51\x01':
            if len(frame) < 37 or frame[-24:-19] != OBSERVED_TRAILER_PREFIX:
                raise ValueError('Unexpected application routing trailer')
            counter = next_counter()
            if not 1 <= counter <= 0xffffffff:
                raise ValueError('Routing counter exhausted')
            updated = bytearray(frame)
            updated[6:8] = (counter & 0xffff).to_bytes(2, 'little')
            updated[-19:-15] = counter.to_bytes(4, 'little')
            updated[-2:] = crc(updated[:-2], 0x3692, 0x8408).to_bytes(2, 'little')
            frame = bytes(updated)
        result.append(frame)
    return b''.join(result)


def historical_next_counter(captures):
    highest = 0
    for path in Path(captures).glob('control-attempt-*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        packets = [row['hex'] for row in record.get('outgoing_transport', [])
                   if not row.get('retry')]
        packets += [row.get('wire_packet_hex', row.get('packet_hex', ''))
                    for row in record.get('commands', [])]
        for encoded in packets:
            try:
                raw = bytes.fromhex(encoded)
            except ValueError:
                continue
            if len(raw) < 20 or raw[6] not in (4, 5):
                continue
            for frame in valid_frames(raw[20:] if raw[6] == 5 else raw[34:]):
                if (frame[4:6] == b'\x3b\xe9' and frame[9:11] == b'\x51\x01'
                        and len(frame) >= 37 and frame[-24:-19] == OBSERVED_TRAILER_PREFIX):
                    highest = max(highest, int.from_bytes(frame[-19:-15], 'little'))
    return highest + 1


class RoutingSequenceStore:
    """One active process per routing identity; unused reserved numbers are skipped."""

    def __init__(self, path=STATE_PATH, initial=1, block_size=64):
        if not 1 <= block_size <= 4096:
            raise ValueError('Invalid routing reservation size')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.block_size = block_size
        self._thread_lock = threading.Lock()
        self._file = self.path.with_suffix('.lock').open('a+b')
        self._locked = False
        try:
            self._file.seek(0, 2)
            if self._file.tell() == 0:
                self._file.write(b'0')
                self._file.flush()
            self._file.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('已有控制台占用飞机连接，请先退出旧控制台') from exc
            self._locked = True
            if self.path.exists():
                state = json.loads(self.path.read_text(encoding='utf-8'))
                start = state.get('next_counter')
                if (state.get('version') != 1 or type(start) is not int or
                        state.get('routing_identity') != OBSERVED_TRAILER_PREFIX[:4].hex()):
                    raise ValueError('Invalid routing sequence state; refusing to reuse numbers')
            else:
                start = initial() if callable(initial) else initial
            self.first = start
            self._reserve(start)
        except BaseException:
            self.close()
            raise

    def _reserve(self, start):
        if type(start) is not int or not 1 <= start <= 0xffffffff:
            raise ValueError('Routing counter exhausted or invalid')
        end = min(start + self.block_size, 0x100000000)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as target:
            json.dump({'version': 1, 'next_counter': end,
                       'routing_identity': OBSERVED_TRAILER_PREFIX[:4].hex()}, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.path)
        self._cursor, self._end = start, end

    def next(self):
        with self._thread_lock:
            if self._file is None:
                raise RuntimeError('Routing sequence store is closed')
            if self._cursor >= self._end:
                self._reserve(self._end)
            result = self._cursor
            self._cursor += 1
            return result

    def status(self):
        with self._thread_lock:
            return {'first': self.first, 'next': self._cursor,
                    'reserved_until': self._end, 'persistent': True}

    def close(self):
        if self._file is not None:
            if self._locked:
                self._file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
