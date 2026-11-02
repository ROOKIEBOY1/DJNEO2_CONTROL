"""Inspect the captured 51/13 messages on both sides of new sessions."""

import argparse

from transition_events import read_events


def summarize(path, session):
    handshake, events = read_events(path, session)
    print('session', f'0x{session:04x}')
    for direction, sender, receiver in (('app', 0xee, 0xe9),
                                         ('plane', 0xe9, 0xee)):
        rows = [event for event in events if event[1] == direction
                and event[2:6] == (sender, receiver, 0x51, 0x13)
                and handshake <= event[0] < handshake + 3]
        print(direction, 'count_first_3s', len(rows))
        payloads = [bytes.fromhex(event[8]) for event in rows]
        if payloads:
            varying = [offset for offset in range(len(payloads[0]))
                       if len({payload[offset] for payload in payloads
                               if len(payload) == len(payloads[0])}) > 1]
            print('  payload_length', len(payloads[0]),
                  'varying_byte_offsets', varying)
        for event in rows[:3]:
            print('  ms', round((event[0] - handshake) * 1000),
                  'flags_unknown', 'sequence', f'0x{event[7]:04x}',
                  'payload', event[8])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
