"""Summarize one saved live control attempt without printing raw device data."""

import collections
import datetime
import json
import sys

from control_packets import valid_frames


def local_time(value):
    return datetime.datetime.fromtimestamp(value).strftime('%H:%M:%S.%f')[:-3]


record = json.loads(open(sys.argv[1], encoding='utf-8').read())
commands = record['commands']
takeoff_at = next((command['time'] for command in commands if command.get('param') == 1), None)
if takeoff_at is None:
    raise SystemExit('No takeoff command; use analyze_device_query_attempt.py for query-only sessions')
incoming = record['incoming']
if 'incoming_control' in record:
    incoming = sorted(record['incoming_control'] +
                      [row for row in incoming if row['channel'] != 1],
                      key=lambda row: row['time'])
print('status', record['status'])
print('commands', [(local_time(c['time']), c.get('param', c.get('command')), hex(c['inner_sequence']),
                    len(bytes.fromhex(c.get('wire_packet_hex', c['packet_hex'])))) for c in commands])
print('stored_incoming', len(record['incoming']), 'truncated', record['incoming_truncated'])
for label, start, end in (
    ('before', float('-inf'), takeoff_at),
    ('after_2s', takeoff_at, takeoff_at + 2),
    ('after_later', takeoff_at + 2, float('inf')),
):
    rows = [row for row in incoming if start <= row['time'] < end]
    lengths = collections.Counter(len(bytes.fromhex(row['hex'])) for row in rows)
    channels = collections.Counter(row['channel'] for row in rows)
    shapes = collections.Counter()
    fly = []
    for row in rows:
        raw = bytes.fromhex(row['hex'])
        if len(raw) < 34 or row['channel'] != 1:
            continue
        for outer in valid_frames(raw[34:]):
            frames = (list(valid_frames(outer[11:-2]))
                      if outer[4:6] == b'\x27\xee' and outer[9:11] == b'\x51\x01'
                      else [outer])
            for frame in frames:
                key = (frame[4], frame[5], frame[9], frame[10], len(frame) - 13)
                shapes[key] += 1
                if frame[9:11] == b'\x03\x2a':
                    fly.append((local_time(row['time']), key,
                                hex(int.from_bytes(frame[6:8], 'little')),
                                frame[11:-2].hex()))
    print(label, 'packets', len(rows), 'channels', dict(channels),
          'lengths', lengths.most_common(8), 'DUML_shapes', len(shapes),
          'top', shapes.most_common(15), 'fly', fly)
