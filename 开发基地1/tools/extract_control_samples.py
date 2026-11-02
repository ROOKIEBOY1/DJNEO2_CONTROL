"""Print compact real takeoff and non-neutral stick samples from local captures."""

import struct
import sys
from pcapng import FileScanner, blocks
from transition_events import iter_frames


def axes(payload):
    bits = int.from_bytes(payload, 'little')
    return tuple((bits >> (24 + 11 * k)) & 2047 for k in range(4))


seen_sticks = 0
seen_fly = 0
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
        for outer in iter_frames(packet[34:]):
            if outer[4:6] == b'\x02\xa9' and outer[9:11] == b'\x01\x0a' and len(outer) == 41:
                payload = outer[11:-2]
                values = axes(payload)
                if abs(values[1] - 1024) > 200 and len(packet) == 75 and seen_sticks < 2:
                    print('STICK', 'session', hex(int.from_bytes(packet[2:4], 'little')),
                          'axes', values, 'payload', payload.hex(),
                          'packet', packet.hex())
                    seen_sticks += 1
            if outer[4:6] == b'\x3b\xe9' and outer[9:11] == b'\x51\x01':
                for inner in iter_frames(outer[11:-2]):
                    if inner[4:6] == b'\x02\x03' and inner[9:11] == b'\x03\x2a' and seen_fly < 4:
                        print('FLY', 'session', hex(int.from_bytes(packet[2:4], 'little')),
                              'outer_seq', hex(int.from_bytes(outer[6:8], 'little')),
                              'inner_seq', hex(int.from_bytes(inner[6:8], 'little')),
                              'param', inner[11:-2].hex(),
                              'trailer', outer[11 + len(inner):-2].hex(),
                              'outer_frame', outer.hex())
                        seen_fly += 1
