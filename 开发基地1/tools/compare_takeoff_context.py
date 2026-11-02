"""Compare the live computer takeoff packet with the labeled DJI Fly packet."""

import json
import struct
import sys

from pcapng import FileScanner, blocks
from control_packets import valid_frames


live = json.loads(open(sys.argv[1], encoding='utf-8').read())
computer = bytes.fromhex(next(c['packet_hex'] for c in live['commands'] if c['param'] == 1))
print('COMPUTER', 'len', len(computer), 'header', computer[:34].hex())
last_video = None
for index, frame in enumerate(valid_frames(computer[34:])):
    print(' COMPUTER_FRAME', index, 'len', len(frame), 'key', frame[4:6].hex(),
          frame[9:11].hex(), 'seq', frame[6:8].hex(),
          'prefix', frame[:20].hex())

with open(sys.argv[2], 'rb') as source:
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
        if len(packet) >= 16 and packet[6] == 2 and src_port == 9003:
            last_video = (packet[4:6].hex(), packet[8:12].hex())
        if len(packet) < 34 or packet[6] != 4:
            continue
        frames = list(valid_frames(packet[34:]))
        for index, frame in enumerate(frames):
            if frame[4:6] != b'\x3b\xe9' or frame[9:11] != b'\x51\x01':
                continue
            inner = list(valid_frames(frame[11:-2]))
            if any(x[4:6] == b'\x02\x03' and x[9:11] == b'\x03\x2a' and x[11:-2] == b'\x01' for x in inner):
                print('DJI_FLY', 'len', len(packet), 'header', packet[:34].hex(),
                      'frame_count', len(frames), 'takeoff_index', index,
                      'last_video', last_video)
                for i, item in enumerate(frames):
                    print(' DJI_FRAME', i, 'len', len(item), 'key', item[4:6].hex(),
                          item[9:11].hex(), 'seq', item[6:8].hex(),
                          'prefix', item[:20].hex())
                sys.exit(0)
