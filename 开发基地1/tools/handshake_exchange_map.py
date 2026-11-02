"""Compare application registration exchanges without printing device identifiers."""

import argparse
import collections
import hashlib

from transition_events import read_events


def fingerprint(payload):
    return hashlib.sha256(bytes.fromhex(payload)).hexdigest()[:12]


def inspect(path, session):
    handshake, events = read_events(path, session)
    start = min(e[0] for e in events if e[1] == 'app' and e[0] >= handshake)
    rows = [e for e in events if start <= e[0] < start + 4
            and e[4] == 0x51 and e[2:4] in ((0xee, 0xe9), (0xe9, 0xee))]
    print('session', f'0x{session:04x}', 'registration_frames', len(rows))
    groups = collections.defaultdict(list)
    for e in rows:
        groups[(e[1], e[5], e[6], e[8][:4])].append(e)
    fingerprints = {}
    for key, values in sorted(groups.items()):
        hashes = {fingerprint(e[8]) for e in values}
        fingerprints[key] = hashes
        print('group', key, 'count', len(values), 'unique_payloads', len(hashes),
              'first_ms', round((values[0][0] - start) * 1000))
    for request in [e for e in rows if e[1] == 'app' and e[5] != 0x13][:20]:
        replies = [e for e in rows if e[1] == 'plane' and e[5] == request[5]
                   and e[7] == request[7] and 0 <= e[0] - request[0] <= 1]
        print('request', f'51/{request[5]:02x}', 'length', request[6],
              'operation_prefix', request[8][:4], 'matched_replies', len(replies),
              'reply_lengths', [e[6] for e in replies])
    return fingerprints


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_session', nargs='+', help='capture.pcapng:0xSESSION')
    args = parser.parse_args()
    previous = None
    for spec in args.capture_session:
        path, session = spec.rsplit(':', 1)
        current = inspect(path, int(session, 0))
        if previous is not None:
            print('shared_exact_payload_counts')
            for key in sorted(previous.keys() & current.keys()):
                print(key, len(previous[key] & current[key]))
        previous = current
