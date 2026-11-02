"""Resolve a live unknown registration request from an independent own-device capture."""

import argparse
import hashlib
import json
from pathlib import Path

from build_registration_profile import as_template
from capture_messages import duml_messages, udp_records
from registration_exchange import PROFILE


def extend(log_path, capture, session, write=False, response_length=None):
    profile = json.loads(PROFILE.read_text(encoding='utf-8'))
    live = json.loads(Path(log_path).read_text(encoding='utf-8'))
    unknown = {}
    for event in live.get('incoming_control', live['incoming']):
        for frame, _ in duml_messages(bytes.fromhex(event['hex'])):
            if frame[4:6] != b'\xe9\xee' or frame[9] != 0x51 or frame[8] != 0x40:
                continue
            key = f'{frame[10]:02x}:' + hashlib.sha256(frame[11:-2]).hexdigest()
            if key not in profile['response_rules']:
                unknown[key] = frame
    records = []
    for timestamp, direction, raw in udp_records(capture):
        if int.from_bytes(raw[2:4], 'little') != session:
            continue
        records.extend((timestamp, direction, frame, wrapper)
                       for frame, wrapper in duml_messages(raw))
    added = 0
    for key, request in unknown.items():
        matches = [(t, f) for t, d, f, _ in records if d == 'plane'
                   and f[4:6] == b'\xe9\xee' and f[9:11] == request[9:11]
                   and f[8] == 0x40 and f[11:-2] == request[11:-2]]
        candidates = {}
        for timestamp, captured in matches:
            for time, direction, frame, wrapper in records:
                if (direction == 'app' and 0 <= time - timestamp <= 1 and
                        frame[4:6] == b'\xee\xe9' and frame[8] == 0xc0 and
                        frame[9:11] == captured[9:11] and frame[6:8] == captured[6:8]
                        and (response_length is None or len(frame) - 13 == response_length)):
                    candidates[frame[11:-2]] = as_template(frame, wrapper)
        print('request', request[9:11].hex(), 'length', len(request) - 13,
              'capture_matches', len(matches), 'distinct_responses', len(candidates),
              'response_lengths', [len(payload) for payload in candidates])
        if len(candidates) != 1:
            continue
        template = next(iter(candidates.values()))
        name = f'response_{request[10]:02x}:{len(request) - 13}:{key[-64:-52]}'
        print('verified_response_length', len(bytes.fromhex(template['payload_hex'])))
        profile['templates'][name] = template
        profile['response_rules'][key] = name
        added += 1
    if write and added:
        profile.setdefault('additional_sources', []).append({
            'capture': Path(capture).name,
            'sha256': hashlib.sha256(Path(capture).read_bytes()).hexdigest(),
            'session': f'0x{session:04x}', 'resolved_log': Path(log_path).name})
        PROFILE.write_text(json.dumps(profile, indent=2), encoding='utf-8')
        print('Updated own-device profile, rules added:', added)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log')
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--response-length', type=int)
    args = parser.parse_args()
    extend(args.log, args.capture, args.session, args.write, args.response_length)
