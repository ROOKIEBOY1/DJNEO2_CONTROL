"""Inventory every UDP/9003 transport type, including frames rejected by old parsing."""

import argparse
import collections
import struct

from pcapng import FileScanner, blocks


def summarize(path, session):
    counts = collections.Counter()
    samples = {}
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
            src_port, dst_port, udp_length = struct.unpack_from('!HHH', ip, offset)
            if 9003 not in (src_port, dst_port):
                continue
            packet = ip[offset + 8:offset + udp_length]
            if len(packet) < 8 or int.from_bytes(packet[2:4], 'little') != session:
                continue
            direction = 'plane' if src_port == 9003 else 'app'
            length_ok = int.from_bytes(packet[:2], 'little') & 0x7fff == len(packet)
            key = direction, packet[6], length_ok
            counts[key] += 1
            if key not in samples:
                samples[key] = (len(packet), packet[:40].hex())
    for key, count in counts.most_common():
        print(key, count, 'first', samples[key])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
