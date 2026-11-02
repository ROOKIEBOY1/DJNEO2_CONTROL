"""Read observed Neo 2 outer packets without making a network connection."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OuterPacket:
    session: int
    channel: int
    header_length: int
    body: bytes
    sequence: int


def parse_outer(packet: bytes) -> OuterPacket:
    if len(packet) < 8:
        raise ValueError('Outer packet shorter than the common header')
    length = int.from_bytes(packet[:2], 'little')
    if not length & 0x8000 or length & 0x7fff != len(packet):
        raise ValueError('Outer packet length mismatch')
    checksum = 0
    for byte in packet[:7]:
        checksum ^= byte
    if checksum != packet[7]:
        raise ValueError('Outer packet XOR mismatch')
    header_length = 16 if len(packet) >= 16 else 8
    return OuterPacket(
        session=int.from_bytes(packet[2:4], 'little'),
        channel=packet[6],
        header_length=header_length,
        body=packet[header_length:],
        sequence=int.from_bytes(packet[4:6], 'little'),
    )


def decode_handshake(packet: OuterPacket) -> str:
    if packet.channel != 0 or packet.header_length != 16 or len(packet.body) != 32:
        raise ValueError('Not an observed 48-byte handshake')
    return packet.body.hex()


def summarize_exchange(events):
    handshake = None
    short_reply = None
    for timestamp, direction, raw in events:
        try:
            packet = parse_outer(raw)
        except ValueError:
            continue
        if direction == 'to' and packet.channel == 0 and packet.header_length == 16 and len(packet.body) == 32:
            handshake = (timestamp, packet.session)
        elif (direction == 'from' and packet.channel == 0 and packet.header_length == 8
              and packet.body == bytes.fromhex('010f000505401f') and handshake is not None
              and packet.session == handshake[1] and short_reply is None):
            short_reply = timestamp
    if handshake is None:
        raise ValueError('No observed handshake')
    return {
        'session': handshake[1],
        'short_reply_delay_ms': None if short_reply is None else round((short_reply - handshake[0]) * 1000, 1),
    }
