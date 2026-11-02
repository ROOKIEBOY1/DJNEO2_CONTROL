"""Observed application-registration fields, independent of opaque capability values."""


def channel_offer(payload):
    """51/41: opcode[3], peer-id[4], byte-count:u16, (id:u8,size:u8,value)*."""
    if (len(payload) < 9 or payload[:3] != b'\x00\x02\x00' or
            int.from_bytes(payload[7:9], 'little') != len(payload) - 9):
        raise ValueError('Not a complete channel offer')
    records = []
    offset = 9
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ValueError('Truncated channel record')
        channel, size = payload[offset:offset + 2]
        end = offset + 2 + size
        if not size or end > len(payload):
            raise ValueError('Invalid channel record length')
        records.append((channel, payload[offset + 2:end]))
        offset = end
    return records


def channel_acknowledgement(payload, template):
    if (len(template) < 13 or template[:5] != b'\x00\x00\x00\x02\x00' or
            int.from_bytes(template[9:11], 'little') != len(template) - 11 or
            (len(template) - 11) % 2):
        raise ValueError('Invalid local channel acknowledgement template')
    supported = [int.from_bytes(template[i:i + 2], 'little')
                 for i in range(11, len(template), 2)]
    supported = [channel for channel in supported if channel]
    offered = {channel for channel, _ in channel_offer(payload)}
    accepted = [channel for channel in supported if channel in offered]
    body = b''.join(channel.to_bytes(2, 'little') for channel in accepted) + bytes(2)
    return template[:9] + len(body).to_bytes(2, 'little') + body


def identity_acknowledgement(payload, template):
    if (len(payload) != 51 or len(template) != 24 or payload[:2] != b'\x00\x00' or
            payload[:23] != template[:23]):
        raise ValueError('Identity request does not match this aircraft')
    return payload[:23] + b'\x00'
