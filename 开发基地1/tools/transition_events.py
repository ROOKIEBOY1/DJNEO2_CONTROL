"""Summarize validated DUML traffic around the first full 01/0A frame."""

import collections
import struct
import sys

from pcapng import FileScanner, blocks
from offline_takeoff import crc


def iter_frames(body):
    offset = 0
    while offset + 13 <= len(body):
        if body[offset] != 0x55:
            offset += 1
            continue
        size = body[offset + 1] | ((body[offset + 2] & 3) << 8)
        frame = body[offset:offset + size]
        if (size < 13 or len(frame) != size
                or crc(frame[:3], 0x77, 0x8c) != frame[3]
                or crc(frame[:-2], 0x3692, 0x8408) != int.from_bytes(frame[-2:], 'little')):
            offset += 1
            continue
        yield frame
        offset += size


def read_events(path, session, require_handshake=True):
    handshake = None
    events = []
    with open(path, 'rb') as source:
        for block in FileScanner(source):
            if not isinstance(block, blocks.EnhancedPacket):
                continue
            data = block.packet_data
            if len(data) < 42 or data[12:14] != b'\x08\x00':
                continue
            ip = data[14:]
            if ip[9] != 17:
                continue
            offset = (ip[0] & 15) * 4
            if len(ip) < offset + 8:
                continue
            src_port, dst_port = struct.unpack_from('!HH', ip, offset)
            if src_port != 9003 and dst_port != 9003:
                continue
            packet = ip[offset + 8:]
            if len(packet) < 16 or int.from_bytes(packet[2:4], 'little') != session:
                continue
            direction = 'plane' if src_port == 9003 else 'app'
            if packet[6] == 0 and len(packet) == 48 and direction == 'app' and handshake is None:
                handshake = block.timestamp
            if len(packet) < 34 or packet[6] not in (1, 4):
                continue
            for frame in iter_frames(packet[34:]):
                frames = (list(iter_frames(frame[11:-2]))
                          if frame[4:6] in (b'\x27\xee', b'\x3b\xe9') and frame[9:11] == b'\x51\x01'
                          else [frame])
                for inner in frames:
                    events.append((block.timestamp, direction, inner[4], inner[5],
                                   inner[9], inner[10], len(inner) - 13,
                                   int.from_bytes(inner[6:8], 'little'), inner[11:-2].hex()))
    if handshake is None and require_handshake:
        raise ValueError('No app handshake')
    return handshake, events


def label(event):
    _, direction, sender, receiver, command_set, command_id, length, sequence, _ = event
    return f'{direction} {sender:02x}>{receiver:02x} {command_set:02x}/{command_id:02x} len={length}'


if __name__ == '__main__':
    handshake, events = read_events(sys.argv[1], int(sys.argv[2], 0))
    first_short = next(e[0] for e in events if e[1] == 'app' and e[4:7] == (1, 10, 18))
    first_full = next(e[0] for e in events if e[1] == 'app' and e[4:7] == (1, 10, 28))
    print('first_short_s', round(first_short - handshake, 3),
          'first_full_s', round(first_full - handshake, 3))
    short_sequences = {e[7] for e in events if e[1] == 'app' and e[4:7] == (1, 10, 18)}
    matching_replies = [e for e in events if e[1] == 'plane' and e[2:6] == (0xa9, 2, 1, 10)
                        and e[7] in short_sequences]
    print('short_frames', len(short_sequences), 'matching_a9_replies', len(matching_replies))
    if '--brief' in sys.argv[3:]:
        app_before = [e for e in events if handshake <= e[0] < first_full and e[1] == 'app']
        counts = collections.Counter(label(e) for e in app_before)
        print('app_frames_before_full', len(app_before), 'unique_shapes', len(counts))
        for name, count in counts.most_common(20):
            print(' ', count, name)
        sys.exit(0)
    for title, start, end in (
        ('before_short', handshake, first_short),
        ('short_phase', first_short, first_full),
        ('last_half_second', first_full - 0.5, first_full),
        ('next_half_second', first_full, first_full + 0.5),
    ):
        selected = [e for e in events if start <= e[0] < end and e[2:4] != (0x3b, 0xe9)]
        counts = collections.Counter(label(e) for e in selected)
        print(title, 'frames', len(selected), 'unique', len(counts))
        for name, count in counts.most_common(12):
            print(' ', count, name)
    print('last 30 nonvideo before full')
    selected = [e for e in events if first_full - 0.25 <= e[0] < first_full
                and e[2:4] != (0x3b, 0xe9)]
    for e in selected[-30:]:
        print(round(e[0] - handshake, 3), label(e), f'seq={e[7]:04x}', e[8][:20])
    print('app nonstick last half second')
    selected = [e for e in events if first_full - 0.5 <= e[0] < first_full
                and e[1] == 'app' and e[4:6] != (1, 10)]
    counts = collections.Counter(label(e) for e in selected)
    for name, count in counts.most_common():
        print(' ', count, name)
    for title, direction in (('app_last_100ms', 'app'), ('plane_last_100ms', 'plane')):
        selected = [e for e in events if first_full - 0.1 <= e[0] < first_full
                    and e[1] == direction and e[4:6] != (1, 10)]
        counts = collections.Counter(label(e) for e in selected)
        print(title, 'frames', len(selected), 'unique', len(counts))
        for name, count in counts.most_common():
            print(' ', count, name)
