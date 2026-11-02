"""Optional local pcapng reader for offline video validation."""
import struct

def udp_records(path):
    from pcapng import FileScanner, blocks

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
            src, dst, length = struct.unpack_from('!HHH', ip, offset)
            if 9003 not in (src, dst):
                continue
            raw = ip[offset + 8:offset + length]
            if len(raw) >= 8:
                yield block.timestamp, 'plane' if src == 9003 else 'app', raw
