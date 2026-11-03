"""Reproduce an observed DUML frame and exercise only local UDP loopback.

This does not construct a complete drone packet or contact the drone.
"""
import socket

def crc(data, initial, polynomial):
    value = initial
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (polynomial if value & 1 else 0)
    return value

def encode_frame(sender, receiver, seq, flags, cmd_set, cmd_id, payload):
    length = 13 + len(payload)
    if length > 1023 or not 0 <= seq <= 65535:
        raise ValueError('Invalid DUML length or sequence')
    data = bytearray([0x55, length & 255, 4 | (length >> 8), 0,
                      sender, receiver, seq & 255, seq >> 8, flags, cmd_set, cmd_id])
    data[3] = crc(data[:3], 0x77, 0x8c)
    data.extend(payload)
    data.extend(crc(data, 0x3692, 0x8408).to_bytes(2, 'little'))
    return bytes(data)

def build_takeoff(seq):
    return encode_frame(2, 3, seq, 0x40, 3, 0x2a, b'\x01')

def loopback_exchange(payload):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(2)
        sender.sendto(payload, receiver.getsockname())
        return receiver.recvfrom(65535)[0]

