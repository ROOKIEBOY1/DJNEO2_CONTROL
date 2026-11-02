"""Offline landing evidence. OSD fields are provisional on Neo 2, never actuator gates."""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path
from capture_messages import duml_messages
from landing_status import LandingStatus

def clock(value):
    return datetime.datetime.fromtimestamp(value).strftime('%H:%M:%S.%f')[:-3]

def analyze(path):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = record.get('incoming_control', record.get('incoming', []))
    samples, replies = [], []
    tracker = LandingStatus()
    commands = iter(sorted(record['commands'], key=lambda c: c['time']))
    next_command = next(commands, None)
    transitions = []
    last_phase = None
    for row in rows:
        while next_command is not None and next_command['time'] <= row['time']:
            if next_command.get('param') == 1:
                tracker.new_flight()
            elif next_command.get('param') == 2:
                tracker.begin(next_command['time'])
            next_command = next(commands, None)
        for frame, _ in duml_messages(bytes.fromhex(row['hex'])):
            tracker.observe(frame, row['time'])
            observation = tracker.snapshot(row['time'])
            if observation['phase'] != last_phase:
                transitions.append(dict(observation, at=clock(row['time'])))
                last_phase = observation['phase']
            payload = frame[11:-2]
            if frame[4:6] == b'\x03\x02' and frame[9:11] == b'\x03\x2a' and frame[8] & 0x80:
                replies.append({'at': clock(row['time']), 'time': row['time'],
                                'sequence': int.from_bytes(frame[6:8], 'little'),
                                'payload': payload.hex()})
            if frame[9:11] == b'\x03\x43' and len(payload) >= 44:
                samples.append({'time': row['time'], 'at': clock(row['time']),
                                'sender_receiver': frame[4:6].hex(),
                                'height_candidate_m': int.from_bytes(payload[16:18], 'little', signed=True)/10,
                                'vz_candidate_m_s': int.from_bytes(payload[22:24], 'little', signed=True)/10,
                                'mode_candidate': payload[30] & 0x7f,
                                'latest_command_candidate': payload[31],
                                'flags_candidate': payload[32:36].hex(),
                                'reason_candidate': payload[37]})
    result = {'source': str(Path(path).resolve()),
              'note': 'OSD offsets follow older public DUML dissector; compare evidence before trusting Neo 2 semantics.',
              'capture_start': clock(min(r['time'] for r in rows)),
              'capture_end': clock(max(r['time'] for r in rows)),
              'control_truncated': record.get('incoming_control_truncated'),
              'transport': record['status']['transport'], 'commands': [], 'osd_samples': len(samples),
              'landing_observation_transitions': transitions}
    for command in record['commands']:
        if 'param' not in command:
            continue
        at = command['time']
        after = [s for s in samples if at <= s['time'] <= at + 40]
        before = [s for s in samples if at - 3 <= s['time'] < at]
        result['commands'].append({'at': clock(at), 'param': command['param'],
            'cycle': command.get('flight_cycle'), 'sequence': command['inner_sequence'],
            'replies': [r for r in replies if r['sequence'] == command['inner_sequence'] and at <= r['time'] <= at+2],
            'before': before[-1:] ,
            'after_every_5s': [next((s for s in after if s['time'] >= at + n), None) for n in (0, 2, 5, 10, 20, 30)],
            'mode_counts_40s': dict(Counter(s['mode_candidate'] for s in after)),
            'latest_command_counts_40s': dict(Counter(s['latest_command_candidate'] for s in after))})
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    args = parser.parse_args()
    result = analyze(args.record)
    output = args.record.with_name(args.record.stem + '-landing-analysis.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print('Saved:', output)
