"""Reconstruct the captured 18-byte 01/0A frame for offline comparison."""

from duml import encode_frame


INITIAL_PAYLOAD = bytes.fromhex('010d00000420000108400002000006550104')


def build_initial_010a_packet(session, video_seed, video_ack, sequence):
    if not 1 <= session <= 0xffff:
        raise ValueError('Invalid session')
    if not all(0 <= value <= 0xffff for value in (video_seed, video_ack, sequence)):
        raise ValueError('Invalid seed, ACK or sequence')
    frame = encode_frame(2, 0xa9, sequence, 0, 1, 0x0a, INITIAL_PAYLOAD)
    seed = video_seed.to_bytes(2, 'little')
    prefix = seed * 2 + b'\x00' * 4 + seed * 2 + b'\x00' * 4
    body = prefix + len(frame).to_bytes(2, 'little') + frame
    header = bytearray(16)
    header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
    header[2:4] = session.to_bytes(2, 'little')
    header[6] = 4
    header[8:12] = video_ack.to_bytes(2, 'little') * 2
    checksum = 0
    for byte in header[:7]:
        checksum ^= byte
    header[7] = checksum
    return bytes(header) + body
