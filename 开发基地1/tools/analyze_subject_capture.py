"""Offline subject-box and native task-start correlation; no flight sending."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
from circle_protocol import deduplicate_records
from analyze_circle_capture import clock
from subject_observation import parse_detection, parse_tracking_box, parse_task_state

LEGACY_SELECTION_HINTS = {0x20, 0x21, 0x22, 0x27, 0x29}

def analyze_subjects(records):
    records = deduplicate_records(records)
    detections, tracks, flight, tasks, starts = [], [], [], [], []
    errors = Counter()
    outgoing_vision = Counter()
    selection_hints = []
    for r in records:
        pair = r['command_set'], r['command_id']
        payload = bytes.fromhex(r['payload'])
        meta = {'at': r['at'], 'local_time': clock(r['at']), 'session': r['session']}
        if r['side'] == 'app':
            if r['sender'] == 2 and r['receiver'] == 0x92 and pair == (0x23, 0x13) and payload == b'\x01\x05\x00':
                starts.append(dict(meta, sequence=r['sequence']))
            if pair[0] == 10:
                outgoing_vision[f'{r["sender"]:02x}>{r["receiver"]:02x} 0a/{pair[1]:02x} n{len(payload)}'] += 1
                if pair[1] in LEGACY_SELECTION_HINTS:
                    selection_hints.append(dict(meta, command_id=pair[1], payload=payload.hex()))
            continue
        try:
            if r['sender'] == 0x92 and r['receiver'] == 2:
                if pair == (10, 0xbc):
                    detections.append(dict(meta, **parse_detection(payload)))
                elif pair == (10, 0x95):
                    tracks.append(dict(meta, **parse_tracking_box(payload)))
                elif pair == (0x23, 0x14):
                    tasks.append(dict(meta, **parse_task_state(payload)))
            elif r['sender'] == 3 and pair == (3, 0x43) and len(payload) >= 36:
                flags = int.from_bytes(payload[32:36], 'little')
                flight.append(dict(meta, motors_on=bool(flags & 8), airborne_flag=bool(flags & 4),
                                   mode_raw=payload[30] & 127, flags_raw=flags,
                                   height_candidate_m=int.from_bytes(payload[16:18], 'little', signed=True)/10))
        except ValueError as exc:
            errors[str(exc)] += 1
    runs = []
    for start in starts:
        session, at = start['session'], start['at']
        end = next((r['at'] for r in starts if r['session'] == session and r['at'] > at), at+30)
        end = min(end, at+30)
        def latest_before(rows):
            eligible = [r for r in rows if r['session'] == session and at-1 <= r['at'] <= at]
            return eligible[-1] if eligible else None
        def first_after(rows, predicate):
            return next((r for r in rows if r['session'] == session and at <= r['at'] < end and predicate(r)), None)
        phase_changes = []
        previous = {}
        for r in tasks:
            if r['session'] != session or not at <= r['at'] < end:
                continue
            if previous.get(r['tail_raw']) != r['payload_hex']:
                phase_changes.append(r)
                previous[r['tail_raw']] = r['payload_hex']
        runs.append(dict(start, prestart_detection=latest_before(detections),
                         prestart_flight=latest_before(flight),
                         first_candidate=first_after(detections, lambda r: bool(r['targets'])),
                         first_motor_on=first_after(flight, lambda r: r['motors_on']),
                         first_airborne=first_after(flight, lambda r: r['airborne_flag']),
                         phase_changes=phase_changes))
    # Compare two independent encodings of the same target's coordinates.
    by_target = defaultdict(list)
    for r in detections:
        for target in r['targets']:
            by_target[(r['session'], target['id'])].append((r['at'], target['bbox']))
    times = {key: [at for at, _ in values] for key, values in by_target.items()}
    differences, examples = [], []
    for r in tracks:
        for target in r['targets']:
            key = r['session'], target['id']
            if key not in by_target:
                continue
            index = bisect_left(times[key], r['at'])
            neighbors = [by_target[key][i] for i in (index-1, index) if 0 <= i < len(by_target[key])]
            closest = min(neighbors, key=lambda sample: abs(sample[0]-r['at']))
            if abs(closest[0]-r['at']) > .06:
                continue
            error = max(abs(target['bbox'][name]-closest[1][name]) for name in ('x', 'y', 'width', 'height'))
            differences.append(error)
            if len(examples) < 3:
                examples.append({'at': clock(r['at']), 'target_id': target['id'],
                                 'float_box': target['bbox'], 'u16_box': closest[1], 'max_component_error': error})
    return {'runs': runs, 'detection_messages': len(detections), 'tracking_box_messages': len(tracks),
            'detection_count_distribution': dict(Counter(r['count'] for r in detections)),
            'target_class_state_raw': [dict(class_raw=key[0], state_raw=key[1], count=count) for key, count in
                                      Counter((t['class_raw'], t['state_raw']) for r in detections for t in r['targets']).items()],
            'box_crosscheck': {'paired_samples': len(differences),
                              'max_component_error': max(differences, default=None),
                              'median_max_component_error': statistics.median(differences) if differences else None,
                              'examples': examples},
            'app_vision_commands': dict(outgoing_vision), 'legacy_selection_hint_requests': selection_hints,
            'unsupported_layouts': dict(errors), 'lock_verified': False,
            'limitations': ['A candidate box does not prove subject lock; class/state enums remain raw.',
                            'Absence of legacy selection commands does not exclude a newer periodic message carrying target data.',
                            'Native Circle start was observed to initiate takeoff from the ground; it is not a nonflight probe.']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('indexes', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = [json.loads(line) for path in args.indexes for line in path.read_text(encoding='utf-8').splitlines()]
    result = analyze_subjects(records)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Subject analysis saved:', args.output)
    for run in result['runs']:
        print('Run:', run['local_time'], 'prestart targets:',
              None if run['prestart_detection'] is None else run['prestart_detection']['count'],
              'first candidate:', None if run['first_candidate'] is None else run['first_candidate']['local_time'])
    print('Coordinate crosscheck:', result['box_crosscheck']['paired_samples'], 'samples')
