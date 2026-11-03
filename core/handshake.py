"""Bounded Neo 2 handshake experiment. Sends no control or stick commands."""

import argparse
import ipaddress
import json
import secrets
import socket
import time

from outer import parse_outer


HANDSHAKE_BODY = bytes.fromhex('140000640000019001c005140000640014006400c00514000064000101040102')
SHORT_REPLY_BODY = bytes.fromhex('010f000505401f')
DRONE_IP = '192.168.2.1'
DRONE_PORT = 9003


def new_video_seed():
    # All four independent handshake captures used an 8-aligned word, and
    # the first subsequent video sequence was this word plus 8.
    return (secrets.randbelow(8191) + 1) * 8


def build_handshake(session, ack_word):
    if not 1 <= session <= 0xffff or not 0 <= ack_word <= 0xffff:
        raise ValueError('Session or header word outside u16 range')
    header = bytearray(16)
    header[:2] = (0x8000 | 48).to_bytes(2, 'little')
    header[2:4] = session.to_bytes(2, 'little')
    header[6] = 0
    header[8:10] = ack_word.to_bytes(2, 'little')
    header[10:16] = bytes.fromhex('64006400c005')
    checksum = 0
    for byte in header[:7]:
        checksum ^= byte
    header[7] = checksum
    return bytes(header) + HANDSHAKE_BODY


def build_idle_packet(session, video_seed):
    """34-byte empty App channel packet observed after DJI Fly handshake."""
    if not 1 <= session <= 0xffff or not 0 <= video_seed <= 0xffff:
        raise ValueError('Session or video seed outside u16 range')
    seed = video_seed.to_bytes(2, 'little')
    header = bytearray(16)
    header[:2] = (0x8000 | 34).to_bytes(2, 'little')
    header[2:4] = session.to_bytes(2, 'little')
    header[6] = 4
    header[8:12] = seed * 2
    checksum = 0
    for byte in header[:7]:
        checksum ^= byte
    header[7] = checksum
    return bytes(header) + seed * 2 + b'\x00' * 4 + seed * 2 + b'\x00' * 6


def validate_drone_local_ip(value):
    addr = ipaddress.ip_address(value)
    if addr not in ipaddress.ip_network('192.168.2.0/24') or value == DRONE_IP:
        raise ValueError('Local IP must be the computer address on the drone Wi-Fi (192.168.2.x)')
    return value


def probe(local_ip, remote_port, remote_ip, session, ack_word, timeout):
    packet = build_handshake(session, ack_word)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        sock.settimeout(timeout)
        sock.sendto(packet, (remote_ip, remote_port))
        try:
            while True:
                raw, peer = sock.recvfrom(2048)
                if peer != (remote_ip, remote_port):
                    continue
                try:
                    response = parse_outer(raw)
                except ValueError:
                    continue
                return {
                    'matched': response.session == session and response.channel == 0
                               and response.header_length == 8 and response.body == SHORT_REPLY_BODY,
                    'reply_hex': raw.hex(),
                    'reply_session': f'0x{response.session:04x}',
                }
        except socket.timeout:
            return {'matched': False, 'reply_hex': None, 'reply_session': None}


def validate_retry_bounds(attempts, interval):
    if not 1 <= attempts <= 60 or not 0.01 <= interval <= 2:
        raise ValueError('Retry bounds exceeded')
    return attempts, interval


def probe_repeated(local_ip, remote_port, remote_ip, session, ack_word, attempts=10,
                   interval=0.5, after_match_seconds=0, idle_seconds=0,
                   idle_interval=0.06):
    """Retry only the handshake on one socket, preserving its return port."""
    validate_retry_bounds(attempts, interval)
    if not 0 <= idle_seconds <= 5 or (idle_seconds > 0 and
       (after_match_seconds < idle_seconds or not 0.05 <= idle_interval <= 1)):
        raise ValueError('Idle phase bounds exceeded')
    packet = build_handshake(session, ack_word)
    seen = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        for sent in range(1, attempts + 1):
            sock.sendto(packet, (remote_ip, remote_port))
            deadline = time.monotonic() + interval
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    raw, peer = sock.recvfrom(2048)
                except socket.timeout:
                    break
                if peer != (remote_ip, remote_port):
                    continue
                if len(seen) < 4:
                    seen.append(raw.hex())
                try:
                    response = parse_outer(raw)
                except ValueError:
                    continue
                if (response.session == session and response.channel == 0 and
                    response.header_length == 8 and response.body == SHORT_REPLY_BODY):
                    followup_channels = {}
                    during_idle_channels = {}
                    after_idle_channels = {}
                    followup_samples = []
                    followup_start = time.monotonic()
                    followup_deadline = followup_start + after_match_seconds
                    idle_end = followup_start + idle_seconds
                    next_idle = followup_start
                    idle_sent = 0
                    idle_packet = build_idle_packet(session, ack_word) if idle_seconds else b''
                    while time.monotonic() < followup_deadline:
                        now = time.monotonic()
                        if idle_seconds and now >= next_idle and now < idle_end:
                            sock.sendto(idle_packet, (remote_ip, remote_port))
                            idle_sent += 1
                            next_idle = followup_start + idle_sent * idle_interval
                        now = time.monotonic()
                        remaining = followup_deadline - now
                        if idle_seconds and next_idle < idle_end:
                            remaining = min(remaining, max(0.001, next_idle - now))
                        if remaining <= 0:
                            break
                        sock.settimeout(remaining)
                        try:
                            next_raw, next_peer = sock.recvfrom(2048)
                        except socket.timeout:
                            continue
                        if next_peer != (remote_ip, remote_port):
                            continue
                        try:
                            next_packet = parse_outer(next_raw)
                            key = str(next_packet.channel)
                        except ValueError:
                            key = 'invalid'
                        followup_channels[key] = followup_channels.get(key, 0) + 1
                        phase = during_idle_channels if time.monotonic() < idle_end else after_idle_channels
                        phase[key] = phase.get(key, 0) + 1
                        if len(followup_samples) < 4:
                            followup_samples.append(next_raw[:64].hex())
                    return {'matched': True, 'attempts_sent': sent,
                            'reply_hex': raw.hex(), 'observed_packets': seen,
                            'followup_channels': followup_channels,
                            'during_idle_channels': during_idle_channels,
                            'after_idle_channels': after_idle_channels,
                            'followup_samples': followup_samples,
                            'idle_packets_sent': idle_sent}
    return {'matched': False, 'attempts_sent': attempts,
            'reply_hex': seen[0] if seen else None, 'observed_packets': seen}
