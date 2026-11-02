"""Offline summary of valid DUML traffic before a captured takeoff command."""

import argparse
from collections import Counter

from transition_events import read_events


def shape(event):
    return (f'{event[2]:02x}>{event[3]:02x} '
            f'{event[4]:02x}/{event[5]:02x} len={event[6]}')


def summarize(path, session):
    _, events = read_events(path, session, require_handshake=False)
    takeoffs = [e for e in events if e[1] == 'app' and e[2:6] == (2, 3, 3, 42)
                and e[8] == '01']
    if not takeoffs:
        raise ValueError('No app takeoff frame in the selected session')
    takeoff = takeoffs[0]
    print('takeoff_sequence', f'0x{takeoff[7]:04x}')
    for seconds in (10, 2):
        before = [e for e in events if takeoff[0] - seconds <= e[0] < takeoff[0]]
        for direction in ('app', 'plane'):
            selected = [e for e in before if e[1] == direction]
            counts = Counter(shape(e) for e in selected)
            print(f'{direction}_{seconds}s', 'frames', len(selected),
                  'shapes', len(counts), 'top', counts.most_common(20))
    previous = [e for e in events if takeoff[0] - 0.2 <= e[0] < takeoff[0]
                and e[1] == 'app' and e[4:6] != (1, 10)]
    print('last_200ms_nonstick')
    for event in previous[-30:]:
        print(round((event[0] - takeoff[0]) * 1000), shape(event),
              f'seq=0x{event[7]:04x}', event[8][:32])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
