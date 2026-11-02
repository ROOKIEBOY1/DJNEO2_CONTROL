"""Extract only reviewed registration templates from the user's own DJI Fly capture."""

import argparse
import hashlib
import json
from pathlib import Path

from capture_messages import duml_messages, udp_records
from registration_exchange import PROFILE


def as_template(frame, wrapper):
    return {'sender': frame[4], 'receiver': frame[5], 'flags': frame[8],
            'command_set': frame[9], 'command_id': frame[10],
            'tunneled': wrapper is not None, 'payload_hex': frame[11:-2].hex()}


def extract(path, session):
    messages = []
    for timestamp, direction, raw in udp_records(path):
        if int.from_bytes(raw[2:4], 'little') != session:
            continue
        for frame, wrapper in duml_messages(raw):
            messages.append((timestamp, direction, frame, wrapper))
    start = min(t for t, direction, _, _ in messages if direction == 'app')
    messages = [m for m in messages if start <= m[0] < start + 4]
    templates = {}
    for _, direction, frame, wrapper in messages:
        if direction != 'app':
            continue
        payload = frame[11:-2]
        name = None
        if frame[4:6] == b'\x02\x07' and frame[9:11] == b'\x07\x93' and len(payload) == 24:
            name = 'register'
        elif frame[4:6] == b'\xee\xe9' and frame[9] == 0x51:
            if frame[10] == 0x13 and len(payload) == 55:
                name = 'heartbeat'
            elif frame[8] == 0x40:
                if frame[10] == 0x34 and len(payload) == 38:
                    name = 'info_tunnel' if wrapper is not None else 'info_direct'
                elif frame[10] == 2 and len(payload) == 11:
                    name = 'open'
                elif frame[10] == 6 and len(payload) == 51 and payload[:2] == b'\x04\x02':
                    name = 'identity'
                elif frame[10] == 0x41 and len(payload) == 47 and payload[:2] == b'\x10\x01':
                    name = 'capabilities'
        if name is not None:
            templates.setdefault(name, as_template(frame, wrapper))
    rules = [(0x41, 13, '2001', 49, '0020'), (0x41, 65, '0002', 19, '0000'),
             (8, 49, '0000', 24, '0000'), (6, 51, '0000', 24, '0000')]
    response_rules = {}
    for command, length, prefix, reply_length, reply_prefix in rules:
        candidates = [as_template(frame, wrapper) for _, direction, frame, wrapper in messages
                      if direction == 'app' and frame[4:6] == b'\xee\xe9'
                      and frame[9:11] == bytes((0x51, command)) and frame[8] == 0xc0
                      and len(frame) - 13 == reply_length
                      and frame[11:13].hex() == reply_prefix]
        if not candidates:
            raise ValueError('Missing registration response template')
        template_name = f'response_{command:02x}:{length}:{prefix}'
        templates[template_name] = candidates[0]
        for _, direction, frame, _ in messages:
            if (direction == 'plane' and frame[4:6] == b'\xe9\xee' and
                    frame[9:11] == bytes((0x51, command)) and frame[8] == 0x40
                    and len(frame) - 13 == length and frame[11:13].hex() == prefix):
                key = f'{command:02x}:' + hashlib.sha256(frame[11:-2]).hexdigest()
                response_rules[key] = template_name
    required = {'register', 'heartbeat', 'info_direct', 'info_tunnel', 'open',
                'identity', 'capabilities'}
    if not required <= templates.keys():
        raise ValueError('Incomplete capture-derived registration profile')
    return {'source_capture': Path(path).name,
            'source_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'source_session': f'0x{session:04x}', 'templates': templates,
            'response_rules': response_rules}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda v: int(v, 0))
    parser.add_argument('--output', type=Path, default=PROFILE)
    args = parser.parse_args()
    profile = extract(args.capture, args.session)
    args.output.parent.mkdir(exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2), encoding='utf-8')
    print('Saved registration profile:', args.output, 'templates:', len(profile['templates']))
