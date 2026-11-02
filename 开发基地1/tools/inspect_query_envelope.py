"""Print captured flight-controller query and reply envelope samples offline."""

import argparse
import struct

from pcapng import FileScanner, blocks
from transition_events import iter_frames


def inspect(path, session, command_set, command_id, target):
    seen = set()
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
            if 9003 not in (src_port, dst_port):
                continue
            packet = ip[offset + 8:]
            if len(packet) < 34 or int.from_bytes(packet[2:4], 'little') != session:
                continue
            direction = 'plane' if src_port == 9003 else 'app'
            if direction in seen:
                continue
            for tunnel in iter_frames(packet[34:]):
                frames = (iter_frames(tunnel[11:-2])
                          if tunnel[4:6] in (b'\x3b\xe9', b'\x27\xee')
                          else (tunnel,))
                for inner in frames:
                    if inner[9:11] != bytes((command_set, command_id)):
                        continue
                    if inner[4:6] not in (bytes((2, target)), bytes((target, 2))):
                        continue
                    print(direction, 'packet_len', len(packet),
                          'outer_header', packet[:34].hex(),
                          'tunnel', tunnel.hex(), 'inner', inner.hex())
                    seen.add(direction)
                    break
                if direction in seen:
                    break
            if len(seen) == 2:
                return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    parser.add_argument('command_set', type=lambda value: int(value, 0))
    parser.add_argument('command_id', type=lambda value: int(value, 0))
    parser.add_argument('--target', type=lambda value: int(value, 0), default=3)
    args = parser.parse_args()
    inspect(args.capture, args.session, args.command_set, args.command_id,
            args.target)
