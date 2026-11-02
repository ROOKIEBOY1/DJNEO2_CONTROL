"""Flight-command UDP packets and matching replies from captured Neo 2 traffic."""

from outer import parse_outer
from fly_command import build_fly_tunnel, build_tunnel_command
from duml import crc, encode_frame


OBSERVED_TRAILER_PREFIX = bytes.fromhex('8e338dbb02')


def build_fly_packet(session, video_seed, video_ack, video_word,
                     outer_sequence, inner_sequence, param):
    if param not in (1, 2):
        raise ValueError('Only captured takeoff and landing parameters are supported')
    frame = build_fly_tunnel(param, inner_sequence, outer_sequence,
                             OBSERVED_TRAILER_PREFIX)
    return _wrap_command(session, video_seed, video_ack, video_word, frame,
                         outer_sequence, inner_sequence)


def build_query_packet(session, video_seed, video_ack, video_word,
                       outer_sequence, inner_sequence):
    """Build the observed zero-payload 03/34 device-information query."""
    frame = build_tunnel_command(3, 0x34, b'', inner_sequence, outer_sequence,
                                 OBSERVED_TRAILER_PREFIX)
    return _wrap_command(session, video_seed, video_ack, video_word, frame,
                         outer_sequence, inner_sequence)


def build_startup_query_packet(session, video_seed, video_ack, video_word,
                               first_sequence):
    """Build the four captured zero-payload startup queries as direct frames."""
    if not 0 <= first_sequence <= 0xffff:
        raise ValueError('Invalid first sequence')
    frames = b''.join(encode_frame(2, target, (first_sequence + index) & 0xffff,
                                  0x40, 0, 1, b'')
                      for index, target in enumerate((0x0e, 0x1f, 0xa2, 0x00)))
    return _wrap_command(session, video_seed, video_ack, video_word, frames,
                         first_sequence, first_sequence)


def _wrap_command(session, video_seed, video_ack, video_word, frame,
                  outer_sequence, inner_sequence):
    if not 1 <= session <= 0xffff or any(not 0 <= value <= 0xffff for value in
                                         (video_seed, video_ack, video_word,
                                          outer_sequence, inner_sequence)):
        raise ValueError('Invalid session, sequence or video field')
    seed = video_seed.to_bytes(2, 'little')
    word = video_word.to_bytes(2, 'little')
    body = (word * 2 + b'\x00' * 4 + seed * 2 + b'\x00' * 4 +
            len(frame).to_bytes(2, 'little') + frame)
    header = bytearray(16)
    header[:2] = (0x8000 | (16 + len(body))).to_bytes(2, 'little')
    header[2:4] = session.to_bytes(2, 'little')
    header[6] = 4
    header[8:12] = video_ack.to_bytes(2, 'little') * 2
    for byte in header[:7]:
        header[7] ^= byte
    return bytes(header) + body


def valid_frames(body):
    offset = 0
    while offset + 13 <= len(body):
        if body[offset] != 0x55:
            offset += 1
            continue
        size = body[offset + 1] | ((body[offset + 2] & 3) << 8)
        frame = body[offset:offset + size]
        if (size < 13 or len(frame) != size or
                crc(frame[:3], 0x77, 0x8c) != frame[3] or
                crc(frame[:-2], 0x3692, 0x8408) != int.from_bytes(frame[-2:], 'little')):
            offset += 1
            continue
        yield frame
        offset += size


def find_fly_reply(raw, session, inner_sequence):
    return find_command_reply(raw, session, inner_sequence, 3, 0x2a)


def find_query_reply(raw, session, inner_sequence):
    return find_command_reply(raw, session, inner_sequence, 3, 0x34)


def find_command_reply(raw, session, inner_sequence, command_set, command_id,
                       sender=3):
    try:
        outer = parse_outer(raw)
    except ValueError:
        return None
    if outer.session != session or outer.channel != 1 or len(raw) < 34:
        return None
    for tunnel in valid_frames(raw[34:]):
        frames = (valid_frames(tunnel[11:-2])
                  if tunnel[4:6] == b'\x27\xee' and tunnel[9:11] == b'\x51\x01'
                  else (tunnel,))
        for frame in frames:
            if (frame[4:6] == bytes((sender, 2)) and
                    frame[8] & 0x80 and
                    frame[9:11] == bytes((command_set, command_id)) and
                    int.from_bytes(frame[6:8], 'little') == inner_sequence):
                return frame[11:-2]
    return None
