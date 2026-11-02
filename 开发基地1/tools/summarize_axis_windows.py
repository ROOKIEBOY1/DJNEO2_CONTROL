"""Show one-second four-axis extrema from a DJI Fly capture."""

import collections
import datetime
import struct
import sys

from pcapng import FileScanner, blocks
from transition_events import iter_frames


buckets = collections.defaultdict(list)
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
        if len(ip) < offset + 8 or struct.unpack_from('!H', ip, offset + 2)[0] != 9003:
            continue
        packet = ip[offset + 8:]
        if len(packet) < 34 or packet[6] != 4:
            continue
        for frame in iter_frames(packet[34:]):
            if frame[4:6] != b'\x02\xa9' or frame[9:11] != b'\x01\x0a' or len(frame) != 41:
                continue
            bits = int.from_bytes(frame[11:-2], 'little')
            values = tuple((bits >> (24 + 11 * index)) & 2047 for index in range(4))
            buckets[int(block.timestamp)].append(values)

for second, values in sorted(buckets.items()):
    ranges = [(min(row[index] for row in values), max(row[index] for row in values)) for index in range(4)]
    if max(max(abs(lo - 1024), abs(hi - 1024)) for lo, hi in ranges) < 150:
        continue
    stamp = datetime.datetime.fromtimestamp(second).strftime('%H:%M:%S')
    print(stamp, 'n', len(values), ' '.join(f'{name}:{lo}-{hi}'
          for name, (lo, hi) in zip(('roll', 'pitch', 'throttle', 'yaw'), ranges)))
