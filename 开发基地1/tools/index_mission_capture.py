"""Index CRC-valid DUML from owned captures. Analysis only; no packet sending."""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path
from capture_messages import duml_messages, udp_records

def index(source, output):
    counts = {'app': Counter(), 'plane': Counter()}
    packets = Counter()
    start = end = None
    incomplete = None
    total = 0
    with output.open('w', encoding='utf-8') as target:
        try:
            for at, side, raw in udp_records(source):
                start = at if start is None else start
                end = at
                packets[side] += 1
                for frame, wrapper in duml_messages(raw):
                    payload = frame[11:-2]
                    key = f'{frame[4]:02x}>{frame[5]:02x} {frame[9]:02x}/{frame[10]:02x} f{frame[8]:02x} n{len(payload)}'
                    counts[side][key] += 1
                    record = {'at': at, 'side': side, 'session': int.from_bytes(raw[2:4], 'little'),
                              'sender': frame[4], 'receiver': frame[5],
                              'command_set': frame[9], 'command_id': frame[10],
                              'sequence': int.from_bytes(frame[6:8], 'little'), 'flags': frame[8],
                              'payload': payload.hex(), 'tunneled': wrapper is not None}
                    target.write(json.dumps(record, separators=(',', ':')) + '\n')
                    total += 1
        except (EOFError, ValueError) as exc:
            incomplete = f'{type(exc).__name__}: {exc}'
    summary = {'source': str(source.resolve()), 'index': str(output.resolve()),
               'start': None if start is None else datetime.datetime.fromtimestamp(start).isoformat(),
               'end': None if end is None else datetime.datetime.fromtimestamp(end).isoformat(),
               'packets': dict(packets), 'frames': total, 'read_warning': incomplete,
               'app_shapes': counts['app'].most_common(), 'plane_shapes': counts['plane'].most_common()}
    output.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(dict(summary, plane_shapes=summary['plane_shapes'][:30]), indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    index(args.source, args.source.with_suffix('.duml.jsonl'))
