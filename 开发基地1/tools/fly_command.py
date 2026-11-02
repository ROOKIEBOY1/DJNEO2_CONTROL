"""Rebuild captured flight-command tunnel frames for offline comparison.

The five-byte trailer prefix is dynamic in the real capture. Its generation
is unknown, so callers must supply it from evidence; this module does not
send packets to an aircraft.
"""

from offline_takeoff import encode_frame


TRAILER_SUFFIX = bytes.fromhex('0000ffffffff018200000000000000')


def build_fly_tunnel(param, inner_sequence, outer_sequence, trailer_prefix):
    if not 0 <= param <= 0xff:
        raise ValueError('Invalid flight command parameter')
    return build_tunnel_command(3, 0x2a, bytes([param]), inner_sequence,
                                outer_sequence, trailer_prefix)


def build_tunnel_command(command_set, command_id, payload, inner_sequence,
                         outer_sequence, trailer_prefix):
    if not 0 <= inner_sequence <= 0xffff or not 0 <= outer_sequence <= 0xffff:
        raise ValueError('Invalid sequence')
    if len(trailer_prefix) != 5:
        raise ValueError('Expected the observed five-byte dynamic trailer prefix')
    inner = encode_frame(2, 3, inner_sequence, 0x40,
                         command_set, command_id, payload)
    trailer = trailer_prefix + outer_sequence.to_bytes(2, 'little') + TRAILER_SUFFIX
    return encode_frame(0x3b, 0xe9, outer_sequence, 0, 0x51, 1, inner + trailer)
