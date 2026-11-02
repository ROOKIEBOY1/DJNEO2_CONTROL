"""Audit actual request/reply completion of application registration transactions."""

import argparse
import json
from pathlib import Path

from capture_messages import duml_messages


def audit(path):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    outgoing = []
    incoming = []
    for row in record.get('outgoing_transport', []):
        if not row.get('retry'):
            outgoing.extend((row['time'], frame) for frame, _ in
                            duml_messages(bytes.fromhex(row['hex'])))
    for row in record.get('incoming_control', record['incoming']):
        incoming.extend((row['time'], frame) for frame, _ in
                        duml_messages(bytes.fromhex(row['hex'])))
    start = outgoing[0][0]
    print(Path(path).name, 'device', record['status'].get('device_verified'))
    for timestamp, frame in outgoing:
        if frame[4:6] != b'\xee\xe9' or frame[9] != 0x51 or frame[8] != 0x40:
            continue
        responses = [(round((t - timestamp) * 1000), len(f) - 13, f[11:15].hex())
                     for t, f in incoming if t >= timestamp and f[4:6] == b'\xe9\xee'
                     and f[9:11] == frame[9:11] and f[6:8] == frame[6:8] and f[8] & 0x80]
        print(round((timestamp - start) * 1000), f'51/{frame[10]:02x}',
              'length', len(frame) - 13, 'seq', frame[6:8].hex(), 'replies', responses[:4])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+')
    for path in parser.parse_args().logs:
        audit(path)
