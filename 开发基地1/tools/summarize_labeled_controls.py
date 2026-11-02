"""Extract flight commands and pitch-axis runs from one labeled iPhone capture."""

import datetime
import struct
import sys

from pcapng import FileScanner, blocks
from transition_events import iter_frames


def stamp(value):
    return datetime.datetime.fromtimestamp(value).strftime('%H:%M:%S.%f')[:-3]


def axes(payload):
    bits = int.from_bytes(payload, 'little')
    return tuple((bits >> (24 + 11 * index)) & 2047 for index in range(4))


commands = []
pitch_runs = []
current = None
neutral_count = 0
with open(sys.argv[1], 'rb') as source:
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
        if len(packet) < 34 or packet[6] not in (1, 4):
            continue
        direction = 'plane' if src_port == 9003 else 'app'
        for outer in iter_frames(packet[34:]):
            if direction == 'app' and outer[4:6] == b'\x02\xa9' and outer[9:11] == b'\x01\x0a' and len(outer) == 41:
                values = axes(outer[11:-2])
                pitch = values[1]
                if abs(pitch - 1024) <= 100:
                    neutral_count += 1
                    if current is not None:
                        pitch_runs.append(current)
                        current = None
                else:
                    sign = 'high' if pitch > 1024 else 'low'
                    if current is None or current['sign'] != sign or block.timestamp - current['last'] > 0.3:
                        if current is not None:
                            pitch_runs.append(current)
                        current = {'sign': sign, 'start': block.timestamp, 'last': block.timestamp,
                                   'count': 0, 'min': pitch, 'max': pitch, 'other_axes': set()}
                    current['last'] = block.timestamp
                    current['count'] += 1
                    current['min'] = min(current['min'], pitch)
                    current['max'] = max(current['max'], pitch)
                    current['other_axes'].add((values[0], values[2], values[3]))
            if outer[4:6] not in (b'\x3b\xe9', b'\x27\xee') or outer[9:11] != b'\x51\x01':
                continue
            for inner in iter_frames(outer[11:-2]):
                if inner[4:6] in (b'\x02\x03', b'\x03\x02') and inner[9:11] == b'\x03\x2a':
                    commands.append((stamp(block.timestamp), direction, inner[4:6].hex(),
                                     hex(int.from_bytes(inner[6:8], 'little')), inner[11:-2].hex(),
                                     outer.hex(), outer[11 + len(inner):-2].hex()))
if current is not None:
    pitch_runs.append(current)

print('flight_commands', len(commands))
for t, direction, devices, sequence, payload, tunnel, trailer in commands:
    print('FLY', t, direction, devices, sequence, 'payload', payload,
          'trailer', trailer, 'tunnel', tunnel)
print('pitch_runs', len(pitch_runs), 'neutral_frames', neutral_count)
for run in pitch_runs:
    print('PITCH', stamp(run['start']), stamp(run['last']), run['sign'],
          'frames', run['count'], 'range', (run['min'], run['max']),
          'other_axes_variants', len(run['other_axes']))
