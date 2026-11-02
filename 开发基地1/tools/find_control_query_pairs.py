"""List flight-controller request/reply pairs before a captured takeoff."""

import argparse
from collections import Counter, defaultdict

from transition_events import read_events


def summarize(path, session):
    _, events = read_events(path, session, require_handshake=False)
    takeoff = next(e for e in events if e[1] == 'app'
                   and e[2:6] == (2, 3, 3, 42) and e[8] == '01')
    replies = defaultdict(list)
    for e in events:
        if e[1] == 'plane' and e[2] == 3 and e[3] == 2:
            replies[(e[4], e[5], e[7])].append(e)
    grouped = defaultdict(list)
    for e in events:
        if not (e[1] == 'app' and e[2] == 2 and e[3] == 3
                and takeoff[0] - 10 <= e[0] < takeoff[0]):
            continue
        found = [r for r in replies[(e[4], e[5], e[7])]
                 if 0 <= r[0] - e[0] <= 0.5]
        grouped[(e[4], e[5], e[6])].append((e, min(found, key=lambda r: r[0])
                                             if found else None))
    print('session', f'0x{session:04x}', 'requests', sum(map(len, grouped.values())))
    for (command_set, command_id, length), pairs in sorted(grouped.items()):
        matched = [r for _, r in pairs if r is not None]
        prefixes = Counter(r[8][:4] for r in matched)
        request_samples = Counter(e[8][:12] for e, _ in pairs)
        print(f'{command_set:02x}/{command_id:02x}', 'length', length,
              'sent', len(pairs), 'replies', len(matched),
              'reply_prefixes', prefixes.most_common(3),
              'request_prefixes', request_samples.most_common(3))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
