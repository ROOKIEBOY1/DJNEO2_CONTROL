"""Check reliable-downlink acknowledgement positions against local Neo 2 captures."""

import argparse
import struct

from pcapng import FileScanner, blocks


def verify(path, session):
    last_reliable = None
    matched = 0
    checked = 0
    samples = []
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
            if len(packet) < 34 or int.from_bytes(packet[2:4], 'little') != session:
                continue
            if src_port == 9003 and packet[6] == 3:
                last_reliable = int.from_bytes(packet[4:6], 'little')
            elif dst_port == 9003 and packet[6] == 4 and last_reliable is not None:
                acknowledged = int.from_bytes(packet[16:18], 'little')
                checked += 1
                matched += acknowledged == last_reliable
                pair = f'{last_reliable:04x}', f'{acknowledged:04x}'
                if pair not in samples and len(samples) < 12:
                    samples.append(pair)
    print('reliable_seq_vs_app_ack16', 'checked', checked,
          'equal_latest', matched, 'samples', samples)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    verify(args.capture, args.session)
