"""Inspect full routing counters and their DUML low words across captured sessions."""

import argparse
from collections import defaultdict

from capture_messages import udp_records
from control_packets import valid_frames


def audit(path):
    sessions = defaultdict(list)
    for timestamp, direction, raw in udp_records(path):
        if direction != 'app' or len(raw) < 34 or raw[6] != 4:
            continue
        session = int.from_bytes(raw[2:4], 'little')
        for frame in valid_frames(raw[34:]):
            if frame[4:6] == b'\x3b\xe9' and frame[9:11] == b'\x51\x01' and len(frame) >= 37:
                sequence = int.from_bytes(frame[6:8], 'little')
                routing = int.from_bytes(frame[-19:-15], 'little')
                sessions[session].append((timestamp, sequence, routing))
    for session, values in sessions.items():
        mismatches = sum(low != (full & 0xffff) for _, low, full in values)
        print('session', f'0x{session:04x}', 'frames', len(values),
              'first', hex(values[0][2]), 'last', hex(values[-1][2]),
              'low_word_mismatches', mismatches,
              'backsteps', sum(b[2] < a[2] for a, b in zip(values, values[1:])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures', nargs='+')
    for item in parser.parse_args().captures:
        print(item)
        audit(item)
