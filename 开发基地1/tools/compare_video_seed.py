"""Compare each observed handshake word with the first video sequence."""

import glob
import struct
import sys
from pathlib import Path
from pcapng import FileScanner, blocks


def packets(path):
    with open(path, 'rb') as source:
        magic = source.read(4)
        source.seek(0)
        if magic == bytes.fromhex('0a0d0d0a'):
            for block in FileScanner(source):
                if isinstance(block, blocks.EnhancedPacket):
                    yield block.packet_data
        else:
            header = source.read(24)
            if len(header) != 24:
                return
            endian = '<' if magic == bytes.fromhex('d4c3b2a1') else '>'
            if struct.unpack_from(endian + 'I', header, 20)[0] != 1:
                return
            while True:
                record = source.read(16)
                if len(record) != 16:
                    return
                size = struct.unpack_from(endian + 'I', record, 8)[0]
                data = source.read(size)
                if len(data) != size:
                    return
                yield data


for pattern in sys.argv[1:]:
    for filename in glob.glob(pattern):
        pending = {}
        found = []
        for data in packets(filename):
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
            if len(packet) < 16 or int.from_bytes(packet[:2], 'little') & 0x7fff != len(packet):
                continue
            session = int.from_bytes(packet[2:4], 'little')
            if dst_port == 9003 and packet[6] == 0 and len(packet) == 48:
                pending.setdefault(session, int.from_bytes(packet[8:10], 'little'))
            if src_port == 9003 and packet[6] == 2 and session in pending:
                seed = pending.pop(session)
                video_seq = int.from_bytes(packet[4:6], 'little')
                found.append((f'0x{session:04x}', f'0x{seed:04x}', f'0x{video_seq:04x}',
                              (video_seq - seed) & 0xffff))
        if found:
            print(Path(filename).name, found[:8])
