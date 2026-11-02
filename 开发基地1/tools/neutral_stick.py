"""Offline reconstruction of a captured four-axis-neutral stick packet."""

from offline_takeoff import encode_frame


NEUTRAL_PAYLOAD = bytes.fromhex('010d0000042000010840000200000655010456080000000000000000')


def build_stick_payload(roll, pitch, throttle, yaw, counter):
    channels = (roll, pitch, throttle, yaw)
    if any(not 364 <= value <= 1684 for value in channels):
        raise ValueError('Channel outside observed range')
    if not 0 <= counter <= 0xffffff:
        raise ValueError('Invalid counter')
    bits = int.from_bytes(NEUTRAL_PAYLOAD, 'little')
    for index, value in enumerate(channels):
        shift = 24 + 11 * index
        bits = (bits & ~(0x7ff << shift)) | (value << shift)
    payload = bytearray(bits.to_bytes(28, 'little'))
    payload[20:23] = counter.to_bytes(3, 'little')
    return bytes(payload)


def build_stick_packet(session, video_seed, video_ack, sequence, counter,
                       roll=1024, pitch=1024, throttle=1024, yaw=1024,
                       video_word=None):
    if not 1 <= session <= 0xffff or not 0 <= video_seed <= 0xffff:
        raise ValueError('Invalid session or video seed')
    if not 0 <= video_ack <= 0xffff or not 0 <= sequence <= 0xffff or not 0 <= counter <= 0xffffff:
        raise ValueError('Invalid ACK, sequence or counter')
    if video_word is None:
        video_word = video_seed
    if not 0 <= video_word <= 0xffff:
        raise ValueError('Invalid video word')
    payload = build_stick_payload(roll, pitch, throttle, yaw, counter)
    frame = encode_frame(2, 0xa9, sequence, 0, 1, 0x0a, payload)
    seed = video_seed.to_bytes(2, 'little')
    word = video_word.to_bytes(2, 'little')
    prefix = word * 2 + b'\x00' * 4 + seed * 2 + b'\x00' * 4
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


def build_neutral_stick_packet(session, video_seed, video_ack, sequence, counter):
    return build_stick_packet(session, video_seed, video_ack, sequence, counter)
