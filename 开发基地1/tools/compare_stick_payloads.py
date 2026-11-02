"""Compare captured full stick payloads with the current neutral template."""

import argparse

from neutral_stick import NEUTRAL_PAYLOAD
from transition_events import read_events


def summarize(specs):
    for spec in specs:
        path, session_text = spec.rsplit(':', 1)
        session = int(session_text, 0)
        _, events = read_events(path, session, require_handshake=False)
        sticks = [bytes.fromhex(e[8]) for e in events if e[1] == 'app'
                  and e[2:7] == (2, 0xa9, 1, 0x0a, 28)]
        if not sticks:
            print(path, 'no full stick payloads')
            continue
        differences = []
        for offset in range(len(NEUTRAL_PAYLOAD)):
            seen = {payload[offset] for payload in sticks if len(payload) == 28}
            if seen != {NEUTRAL_PAYLOAD[offset]}:
                differences.append((offset, len(seen),
                                    [f'{value:02x}' for value in sorted(seen)[:8]],
                                    f'{NEUTRAL_PAYLOAD[offset]:02x}'))
        print(path, f'session=0x{session:04x}', 'sticks', len(sticks),
              'first', sticks[0].hex(), 'diff_offsets', differences)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_session', nargs='+', help='capture.pcapng:0xSESSION')
    summarize(parser.parse_args().capture_session)
