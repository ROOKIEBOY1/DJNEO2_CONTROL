"""Compare query sequences and deeply scan validated frames in connection logs."""

import argparse
from collections import Counter
import json
from pathlib import Path

from offline_takeoff import crc
from control_packets import valid_frames


def all_frames(raw):
    for offset in range(20, len(raw) - 12):
        if raw[offset] != 0x55:
            continue
        size = raw[offset + 1] | ((raw[offset + 2] & 3) << 8)
        if size < 13 or offset + size > len(raw):
            continue
        frame = raw[offset:offset + size]
        if (crc(frame[:3], 0x77, 0x8c) == frame[3] and
                crc(frame[:-2], 0x3692, 0x8408) == int.from_bytes(frame[-2:], 'little')):
            yield offset, frame


def audit(path):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    queries = [c for c in record['commands'] if c.get('command') == 'device_query']
    observed = []
    rows = record.get('incoming_control', record['incoming'])
    for row in rows:
        for offset, frame in all_frames(bytes.fromhex(row['hex'])):
            if frame[9:11] == b'\x03\x34':
                observed.append((f'{frame[4]:02x}>{frame[5]:02x}',
                                 f'{int.from_bytes(frame[6:8], "little"):04x}',
                                 f'{frame[8]:02x}', len(frame) - 13, offset))
    first = queries[0] if queries else {}
    route = None
    if first:
        wire = bytes.fromhex(first.get('wire_packet_hex', first['packet_hex']))
        for frame in valid_frames(wire[20:] if wire[6] == 5 else wire[34:]):
            if frame[4:6] == b'\x3b\xe9' and frame[9:11] == b'\x51\x01':
                route = int.from_bytes(frame[-19:-15], 'little')
    print(Path(path).name, 'verified', record['status'].get('device_verified'),
          'query_inner', f'{first.get("inner_sequence", 0):04x}',
          'query_routing_counter', hex(route) if route is not None else None,
          'queries', len(queries), 'deep_replies', observed[:10],
          'channels', dict(Counter(row['channel'] for row in record['incoming'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+')
    args = parser.parse_args()
    for item in args.logs:
        audit(item)
