"""Find request/reply pairs in the startup window of a captured DJI Fly session."""

import collections
import statistics
import sys

from transition_events import read_events


def summarize(path, session):
    handshake, events = read_events(path, session)
    first_full = next(e[0] for e in events if e[1] == 'app' and e[4:7] == (1, 10, 28))
    requests = [e for e in events if handshake <= e[0] < first_full
                and e[1] == 'app' and e[2] == 2 and e[4:6] == (0, 1) and e[6] == 0]
    responses = collections.defaultdict(list)
    for e in events:
        if e[1] == 'plane' and e[3] == 2 and e[4:6] == (0, 1):
            responses[(e[2], e[7])].append(e)
    grouped = collections.defaultdict(list)
    for req in requests:
        key = (req[3], req[7])
        matches = [r for r in responses[key] if 0 <= r[0] - req[0] <= 2]
        if matches:
            reply = min(matches, key=lambda r: r[0])
            grouped[req[3]].append((req, reply))
        else:
            grouped[req[3]].append((req, None))
    print('session', hex(session), 'requests', len(requests), 'first_full_s',
          round(first_full - handshake, 3))
    for target, pairs in sorted(grouped.items()):
        found = [pair for pair in pairs if pair[1] is not None]
        reply_lengths = collections.Counter(pair[1][6] for pair in found)
        payload_prefixes = collections.Counter(pair[1][8][:2] for pair in found)
        delays = [(pair[1][0] - pair[0][0]) * 1000 for pair in found]
        print(f'  target={target:02x}', 'sent', len(pairs), 'replies', len(found),
              'reply_lengths', dict(reply_lengths),
              'reply_first_byte', dict(payload_prefixes),
              'median_delay_ms', round(statistics.median(delays), 1) if delays else None)


if __name__ == '__main__':
    summarize(sys.argv[1], int(sys.argv[2], 0))
