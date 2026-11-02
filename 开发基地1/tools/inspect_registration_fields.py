"""Locate changing request fields without printing full device identifiers."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path

from capture_messages import duml_messages, udp_records
from registration_exchange import PROFILE


def inspect(log_path, capture, session):
    profile = json.loads(PROFILE.read_text(encoding='utf-8'))
    record = json.loads(Path(log_path).read_text(encoding='utf-8'))
    unknown = {}
    for row in record.get('incoming_control', record['incoming']):
        for frame, _ in duml_messages(bytes.fromhex(row['hex'])):
            if frame[4:6] == b'\xe9\xee' and frame[9] == 0x51 and frame[8] == 0x40:
                key = f'{frame[10]:02x}:' + hashlib.sha256(frame[11:-2]).hexdigest()
                if key not in profile['response_rules']:
                    unknown[key] = frame
    known = {}
    for _, direction, raw in udp_records(capture):
        if direction != 'plane' or int.from_bytes(raw[2:4], 'little') != session:
            continue
        for frame, _ in duml_messages(raw):
            if frame[4:6] != b'\xe9\xee' or frame[9] != 0x51:
                continue
            key = f'{frame[10]:02x}:' + hashlib.sha256(frame[11:-2]).hexdigest()
            if key in profile['response_rules']:
                known[key] = frame
    for key, live in unknown.items():
        payload = live[11:-2]
        candidates = [(sum(a != b for a, b in zip(f[11:-2], payload)), k, f)
                      for k, f in known.items() if f[10] == live[10] and len(f) == len(live)]
        print('unknown', key[:15], 'command', live[9:11].hex(), 'length', len(payload))
        for distance, known_key, frame in sorted(candidates)[:2]:
            old = frame[11:-2]
            print('closest', known_key[:15], 'different_bytes', distance,
                  'offsets_old_new', [(i, f'{a:02x}', f'{b:02x}')
                                      for i, (a, b) in enumerate(zip(old, payload)) if a != b])
            response = bytes.fromhex(profile['templates'][profile['response_rules'][known_key]]['payload_hex'])
            blocks = difflib.SequenceMatcher(None, old, response, autojunk=False).get_matching_blocks()
            print('request_response_shared_blocks', [(b.a, b.b, b.size) for b in blocks if b.size >= 3])
            if live[10] == 0x41:
                print('request_fields', [(i, old[i:i+4].hex()) for i in range(0, len(old), 4)])
            print('response_length', len(response), 'response_fields',
                  [(i, response[i:i+4].hex()) for i in range(0, len(response), 4)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log')
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    inspect(args.log, args.capture, args.session)
