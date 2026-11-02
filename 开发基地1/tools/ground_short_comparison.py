"""Bounded ground comparison: empty channel-4 packets versus captured 18-byte frames."""

import argparse
import datetime
import json
import secrets
import socket
import time
from pathlib import Path

from capture_protocol import parse_outer
from handshake_probe import (DRONE_IP, DRONE_PORT, SHORT_REPLY_BODY,
                             build_handshake, build_idle_packet, new_video_seed,
                             validate_retry_bounds)
from initial_010a import build_initial_010a_packet
from run_handshake_offline import detect_local_ip


def active_packet(mode, session, video_seed, sequence):
    if mode == 'baseline':
        return build_idle_packet(session, video_seed)
    if mode == 'short':
        return build_initial_010a_packet(session, video_seed, video_seed, sequence)
    raise ValueError('Mode must be baseline or short')


def collect_trial(local_ip, remote_ip, remote_port, session, video_seed, mode, first_sequence,
                  attempts=60, retry_interval=0.55, pre_seconds=1.0,
                  active_seconds=2.0, post_seconds=3.0, cadence=0.06):
    """Use one UDP socket; save all matched-session replies and limit every send phase."""
    active_packet(mode, session, video_seed, first_sequence)
    validate_retry_bounds(attempts, retry_interval)
    if not (0 <= pre_seconds <= 2 and 0 < active_seconds <= 2 and
            0 <= post_seconds <= 5 and 0.05 <= cadence <= 1):
        raise ValueError('Phase bounds exceeded')
    handshake = build_handshake(session, video_seed)
    result = {'mode': mode, 'session': f'0x{session:04x}',
              'video_seed': f'0x{video_seed:04x}',
              'first_sequence': f'0x{first_sequence:04x}',
              'matched': False, 'attempts_sent': 0, 'idle_packets_sent': 0,
              'short_packets_sent': 0, 'incoming_counts': {},
              'incoming': [], 'truncated_incoming': False}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        reply = None
        for attempt in range(1, attempts + 1):
            sock.sendto(handshake, (remote_ip, remote_port))
            result['attempts_sent'] = attempt
            deadline = time.monotonic() + retry_interval
            while time.monotonic() < deadline:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    raw, peer = sock.recvfrom(65535)
                except socket.timeout:
                    break
                if peer != (remote_ip, remote_port):
                    continue
                try:
                    packet = parse_outer(raw)
                except ValueError:
                    continue
                if (packet.session == session and packet.channel == 0 and
                        packet.header_length == 8 and packet.body == SHORT_REPLY_BODY):
                    reply = raw
                    break
            if reply is not None:
                break
        if reply is None:
            return result
        result['matched'] = True
        result['reply_hex'] = reply.hex()
        start = time.monotonic()
        phases = (('pre', pre_seconds), ('active', active_seconds), ('post', post_seconds))
        phase_start = start
        short_index = 0
        for phase, duration in phases:
            phase_end = phase_start + duration
            next_send = phase_start
            while time.monotonic() < phase_end:
                now = time.monotonic()
                if phase != 'post' and now >= next_send:
                    if phase == 'pre':
                        outgoing = build_idle_packet(session, video_seed)
                        result['idle_packets_sent'] += 1
                    else:
                        outgoing = active_packet(mode, session, video_seed,
                                                 (first_sequence + short_index) & 0xffff)
                        if mode == 'short':
                            result['short_packets_sent'] += 1
                            short_index += 1
                        else:
                            result['idle_packets_sent'] += 1
                    sock.sendto(outgoing, (remote_ip, remote_port))
                    next_send += cadence
                    if next_send < now:
                        next_send = now + cadence
                remaining = phase_end - time.monotonic()
                if phase != 'post':
                    remaining = min(remaining, max(0.001, next_send - time.monotonic()))
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    raw, peer = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                if peer != (remote_ip, remote_port):
                    continue
                try:
                    packet = parse_outer(raw)
                except ValueError:
                    continue
                if packet.session != session:
                    continue
                key = f'{phase}:{packet.channel}'
                result['incoming_counts'][key] = result['incoming_counts'].get(key, 0) + 1
                if len(result['incoming']) < 1000:
                    result['incoming'].append({'phase': phase,
                                               'elapsed_ms': round((time.monotonic() - start) * 1000, 1),
                                               'channel': packet.channel, 'hex': raw.hex()})
                else:
                    result['truncated_incoming'] = True
            phase_start = phase_end
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true', help='Run both bounded ground trials on drone Wi-Fi')
    args = parser.parse_args()
    if not args.send:
        print('Preview: baseline sends empty channel-4 packets; short trial sends captured 18-byte 01/0A frames. '
              'Each trial uses 1 s empty packets, 2 s comparison traffic, and 3 s passive listening. '
              'Use --send only when the aircraft is stationary on the ground and the computer is on its Wi-Fi.')
        return
    local_ip = detect_local_ip()
    trials = []
    for mode in ('baseline', 'short'):
        session = secrets.randbelow(0xffff) + 1
        seed = new_video_seed()
        sequence = secrets.randbelow(0x10000)
        trial = collect_trial(local_ip, DRONE_IP, DRONE_PORT, session, seed, mode, sequence)
        trials.append(trial)
        if not trial['matched']:
            break
        time.sleep(0.5)
    record = {'local_ip': local_ip, 'trials': trials,
              'interpretation': 'Downlink counts alone do not prove short-frame acceptance.'}
    captures = Path(__file__).resolve().parents[1] / 'captures'
    captures.mkdir(exist_ok=True)
    output = captures / f'ground-short-comparison-{datetime.datetime.now():%Y%m%d-%H%M%S}.json'
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved result: {output}')
    for trial in trials:
        print(json.dumps({key: value for key, value in trial.items() if key != 'incoming'},
                         ensure_ascii=False))


if __name__ == '__main__':
    main()
