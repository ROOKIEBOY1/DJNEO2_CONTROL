"""Locate the first nonbaseline aircraft DUML telemetry after a captured handshake."""

import argparse
from collections import Counter

from transition_events import read_events


def is_baseline(event):
    return (event[2:4] == (0xe9, 0xee) and event[4] == 0x51 or
            event[2:6] == (0x07, 0x02, 0x07, 0x94))


def shape(event):
    return event[2:6] + (event[6],)


def summarize(path, session):
    handshake, events = read_events(path, session)
    downlink = [event for event in events if event[1] == 'plane'
                and event[0] >= handshake and not is_baseline(event)]
    if not downlink:
        raise ValueError('No nonbaseline aircraft DUML telemetry')
    first = downlink[0]
    print('session', f'0x{session:04x}', 'first_rich_after_handshake_ms',
          round((first[0] - handshake) * 1000), 'shape', shape(first))
    for event in downlink[:12]:
        print('plane', round((event[0] - handshake) * 1000), shape(event),
              'payload_prefix', event[8][:24])
    sent = [event for event in events if event[1] == 'app'
            and handshake <= event[0] < first[0]]
    print('app_frames_before_rich', len(sent), 'unique_shapes',
          len({shape(event) for event in sent}))
    print('top', Counter(shape(event) for event in sent).most_common(20))
    print('last_app_before_rich')
    for event in sent[-20:]:
        print('app', round((event[0] - handshake) * 1000), shape(event),
              'payload_prefix', event[8][:24])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
