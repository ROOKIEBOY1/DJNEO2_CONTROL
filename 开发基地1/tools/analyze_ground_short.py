"""Summarize both saved ground trials without sending network traffic."""

import collections
import json
import statistics
import sys

from capture_protocol import parse_outer
from transition_events import iter_frames


def frame_infos(raw):
    if len(raw) < 34:
        return []
    labels = []
    for outer in iter_frames(raw[34:]):
        nested = (list(iter_frames(outer[11:-2]))
                  if outer[4:6] in (b'\x27\xee', b'\x3b\xe9') and outer[9:11] == b'\x51\x01'
                  else [outer])
        for frame in nested:
            labels.append((f'{frame[4]:02x}>{frame[5]:02x} {frame[9]:02x} {frame[10]:02x} len={len(frame)-13}',
                           frame[11:-2].hex()))
    return labels


def summarize(trial):
    print('MODE', trial['mode'], 'session', trial['session'],
          'idle_sent', trial['idle_packets_sent'], 'short_sent', trial['short_packets_sent'])
    for phase in ('pre', 'active', 'post'):
        records = [r for r in trial['incoming'] if r['phase'] == phase]
        lengths = collections.Counter(len(bytes.fromhex(r['hex'])) for r in records)
        channels = collections.Counter(parse_outer(bytes.fromhex(r['hex'])).channel for r in records)
        infos = [info for r in records for info in frame_infos(bytes.fromhex(r['hex']))]
        frames = collections.Counter(label for label, payload in infos)
        times = [r['elapsed_ms'] for r in records]
        gaps = [b-a for a,b in zip(times,times[1:])]
        print(' ', phase, 'count', len(records), 'channels', dict(channels),
              'lengths', sorted(lengths.items()),
              'median_gap_ms', round(statistics.median(gaps),1) if gaps else None)
        print('  DUML', sum(frames.values()), 'shapes', len(frames), 'top', frames.most_common(12))
        for label in frames:
            payloads = collections.Counter(payload for found_label, payload in infos if found_label == label)
            print('   ', label, 'payload_variants', len(payloads))
    all_infos = [info for r in trial['incoming'] for info in frame_infos(bytes.fromhex(r['hex']))]
    for label in sorted({label for label, _ in all_infos}):
        payloads = [bytes.fromhex(payload) for found, payload in all_infos if found == label]
        varying = [index for index in range(len(payloads[0]))
                   if len({payload[index] for payload in payloads}) > 1]
        print(' ', label, 'varying_payload_offsets', varying)
        if label.startswith('e9>ee 51 13'):
            print('  counter_byte_39', [payload[39] for payload in payloads])


if __name__ == '__main__':
    data = json.loads(open(sys.argv[1], encoding='utf-8').read())
    for trial in data['trials']:
        summarize(trial)
    if len(data['trials']) == 2:
        all_infos = [[info for r in trial['incoming']
                      for info in frame_infos(bytes.fromhex(r['hex']))]
                     for trial in data['trials']]
        for label in sorted({name for infos in all_infos for name, _ in infos}):
            values = [[bytes.fromhex(payload) for name, payload in infos if name == label]
                      for infos in all_infos]
            if all(values):
                varying = [index for index in range(len(values[0][0]))
                           if len({payload[index] for group in values for payload in group}) > 1]
                print('ACROSS', label, 'varying_payload_offsets', varying)
