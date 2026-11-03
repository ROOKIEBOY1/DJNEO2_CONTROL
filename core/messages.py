"""Read raw transport and DUML records from local Ethernet pcapng captures."""

import struct

from control_packets import valid_frames


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


def duml_messages(raw):
    if len(raw) < 34 or raw[6] not in (1, 4, 5):
        return
    offset = 20 if raw[6] == 5 else 34
    for frame in valid_frames(raw[offset:]):
        if frame[4:6] in (b'\x3b\xe9', b'\x27\xee') and frame[9:11] == b'\x51\x01':
            for inner in valid_frames(frame[11:-2]):
                yield inner, frame
        else:
            yield frame, None
