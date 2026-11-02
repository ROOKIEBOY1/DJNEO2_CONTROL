"""Correlate this user's labeled Circle capture. Never sends aircraft packets."""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path
from circle_protocol import (CIRCLE_START_PAYLOAD, CIRCLE_SELECTION_PAYLOAD,
                             RADIUS_HASH_BYTES, deduplicate_records,
                             matching_reply, parse_radius_value)

def clock(at):
    return datetime.datetime.fromtimestamp(at, datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='milliseconds')

def describe(record, all_records):
    reply = matching_reply(record, all_records)
    return dict(record, local_time=clock(record['at']), reply=None if reply is None else
                {'local_time': clock(reply['at']), 'payload': reply['payload'],
                 'sequence': reply['sequence'], 'latency_ms': round(1000*(reply['at']-record['at']), 3)})

def confirmed_stop(report, operator_confirmed):
    return bool(operator_confirmed and report['circle_start_sequence'] is not None
                and report['reply'] is not None and report['reply']['payload'] == '00'
                and report['inactive_candidate_at'] is not None)

def analyze(records, operator_confirmed_stop=False):
    unique = deduplicate_records(records)
    app = [r for r in unique if r['side'] == 'app']
    plane = [r for r in unique if r['side'] == 'plane']
    starts, stops, writes, selections, other_task_requests = [], [], [], [], []
    radius_reads = []
    for r in app:
        pair = r['command_set'], r['command_id']
        payload = bytes.fromhex(r['payload'])
        if pair == (0x23, 0x13) and r['receiver'] == 0x92:
            (starts if payload == CIRCLE_START_PAYLOAD else other_task_requests).append(r)
        if pair == (0x23, 0x15) and r['sender'] == 2 and r['receiver'] == 0x92 and r['flags'] == 0x40 and not payload:
            stops.append(r)
        if pair == (3, 0xf9):
            if r['receiver'] == 0x92 and payload[:4] == RADIUS_HASH_BYTES:
                writes.append(describe(r, plane))
            if r['receiver'] == 0x17 and payload == CIRCLE_SELECTION_PAYLOAD:
                selections.append(describe(r, plane))
    for r in plane:
        if (r['sender'], r['receiver'], r['command_set'], r['command_id']) == (0x92, 2, 3, 0xf8):
            try:
                value = parse_radius_value(bytes.fromhex(r['payload']))
            except ValueError:
                continue
            radius_reads.append({'at': r['at'], 'value_m': value})
    read_summary = []
    for value in sorted(set(r['value_m'] for r in radius_reads)):
        group = [r for r in radius_reads if r['value_m'] == value]
        read_summary.append({'value_m': value, 'count': len(group), 'first': clock(group[0]['at']),
                             'last': clock(group[-1]['at'])})
    telemetry = [r for r in plane if (r['sender'], r['receiver'], r['command_set'], r['command_id']) == (0x92, 2, 0x23, 0x14)]
    task_starts = [r for r in app if r['sender'] == 2 and r['receiver'] == 0x92
                   and (r['command_set'], r['command_id']) == (0x23, 0x13) and r['flags'] == 0x40]
    transitions, prior_by_tail = [], {}
    for r in telemetry:
        payload = bytes.fromhex(r['payload'])
        if len(payload) != 5:
            continue
        tail = payload[3:].hex()
        stream_key = (r['session'], tail)
        if prior_by_tail.get(stream_key) != r['payload']:
            transitions.append({'at': r['at'], 'local_time': clock(r['at']), 'payload': r['payload'],
                                'session': r['session'], 'tail_group': tail,
                                'raw_phase': payload[0], 'raw_mode': payload[1]})
            prior_by_tail[stream_key] = r['payload']
    stop_reports = []
    for stop in stops:
        preceding = [r for r in task_starts if r['session'] == stop['session'] and r['at'] < stop['at']]
        recent_start = preceding[-1] if preceding else None
        circle_start = recent_start if recent_start and recent_start['payload'] == CIRCLE_START_PAYLOAD.hex() else None
        if circle_start is not None and any(r['session'] == stop['session']
                and circle_start['at'] < r['at'] < stop['at'] and r['payload'] == '0400000100' for r in telemetry):
            circle_start = None
        inactive = next((r for r in telemetry if r['session'] == stop['session']
                         and stop['at'] <= r['at'] <= stop['at']+3 and r['payload'] == '0000000101'), None)
        stop_reports.append(dict(describe(stop, plane),
            circle_start_sequence=None if circle_start is None else circle_start['sequence'],
            inactive_candidate_at=None if inactive is None else clock(inactive['at']),
            inactive_candidate_timestamp=None if inactive is None else inactive['at'],
            request_to_inactive_ms=None if inactive is None else round(1000*(inactive['at']-stop['at']), 3)))
    runs = []
    for start in starts:
        stop_at = next((r['at'] for r in task_starts if r['session'] == start['session'] and r['at'] > start['at']), float('inf'))
        run_telemetry = [r for r in telemetry if r['session'] == start['session']]
        active = next((r for r in run_telemetry if start['at'] <= r['at'] < stop_at and r['payload'] == '0505000100'), None)
        ending = None if active is None else next((r for r in run_telemetry if active['at'] < r['at'] < stop_at and r['payload'] == '0400000100'), None)
        stop_report = next((r for r in stop_reports if r['session'] == start['session']
                            and r['circle_start_sequence'] == start['sequence']
                            and start['at'] < r['at'] < stop_at), None)
        termination = 'automatic_or_unlabeled'
        if stop_report is not None:
            termination = 'stop_requested_unconfirmed'
            inactive_at = stop_report['inactive_candidate_timestamp']
            if inactive_at is not None and active is not None and inactive_at > active['at']:
                ending = {'at': inactive_at}
                if confirmed_stop(stop_report, operator_confirmed_stop):
                    termination = 'manual_stop'
        axes = Counter()
        if active and ending:
            for r in app:
                if (r['session'] == start['session'] and active['at'] <= r['at'] < ending['at'] and r['sender'] == 2 and r['receiver'] == 0xa9
                        and (r['command_set'], r['command_id']) == (1, 10) and len(r['payload']) == 56):
                    bits = int.from_bytes(bytes.fromhex(r['payload']), 'little')
                    axes[tuple((bits >> (24 + 11*i)) & 2047 for i in range(4))] += 1
        runs.append(dict(describe(start, plane), active_candidate_at=None if active is None else clock(active['at']),
                         termination_candidate=termination,
                         ending_candidate_at=None if ending is None else clock(ending['at']),
                         active_candidate_seconds=None if not active or not ending else round(ending['at']-active['at'], 3),
                         stick_axes_counts=[{'axes': list(k), 'count': v} for k,v in axes.items()]))
    return {'raw_index_records': len(records), 'unique_records': len(unique),
            'duplicate_records': len(records)-len(unique), 'radius_hash_le': RADIUS_HASH_BYTES.hex(),
            'radius_readback_summary': read_summary, 'radius_writes': writes,
            'circle_selection_requests': selections, 'circle_start_requests': runs,
            'other_task_control_requests': [describe(r, plane) for r in other_task_requests],
            'state_transitions_raw': transitions,
            'manual_stop': {
                'confirmed': any(confirmed_stop(r, operator_confirmed_stop) for r in stop_reports),
                'operator_confirmed': operator_confirmed_stop, 'requests': stop_reports,
                'note': 'Confirmation requires a Circle start, stop request, matching 00 reply, subsequent inactive state and operator observation.'}}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('indexes', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--operator-confirmed-stop', action='store_true',
                        help='Use only when the operator confirmed stopping and hovering')
    args = parser.parse_args()
    records = [json.loads(line) for p in args.indexes for line in p.read_text(encoding='utf-8').splitlines()]
    result = analyze(records, operator_confirmed_stop=args.operator_confirmed_stop)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
