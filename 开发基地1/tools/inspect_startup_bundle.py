"""Inspect the captured post-handshake startup bundle without sending it."""

import argparse
import struct

from pcapng import FileScanner, blocks
from transition_events import iter_frames


def inspect(path, session):
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
            dst_port = struct.unpack_from('!H', ip, offset + 2)[0]
            if dst_port != 9003:
                continue
            packet = ip[offset + 8:]
            if (len(packet) < 34 or packet[6] != 4 or
                    int.from_bytes(packet[2:4], 'little') != session):
                continue
            frames = list(iter_frames(packet[34:]))
            shapes = [(frame[4], frame[5], frame[9], frame[10], len(frame) - 13)
                      for frame in frames]
            if shapes[:4] != [(2, target, 0, 1, 0)
                              for target in (0x0e, 0x1f, 0xa2, 0x00)]:
                continue
            print('session', f'0x{session:04x}', 'packet_length', len(packet),
                  'header', packet[:34].hex())
            for index, frame in enumerate(frames):
                print('frame', index, 'shape', shapes[index], 'hex', frame.hex())
            return
    raise ValueError('No four-query startup bundle found')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    inspect(args.capture, args.session)
