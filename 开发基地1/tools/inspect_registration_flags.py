"""Show live request/reply roles in captured 0x51 application registration."""

import argparse

from capture_messages import duml_messages, udp_records


def inspect(path, session):
    start = None
    for timestamp, direction, raw in udp_records(path):
        if int.from_bytes(raw[2:4], 'little') != session:
            continue
        for frame, wrapper in duml_messages(raw):
            if (frame[4:6] not in (b'\xee\xe9', b'\xe9\xee') or
                    frame[9] != 0x51 or frame[10] == 0x13):
                continue
            if start is None:
                start = timestamp
            if timestamp > start + 4:
                return
            print(round((timestamp - start) * 1000), direction,
                  f'51/{frame[10]:02x}', 'flags', f'{frame[8]:02x}',
                  'sequence', f'{int.from_bytes(frame[6:8], "little"):04x}',
                  'length', len(frame) - 13, 'prefix', frame[11:13].hex(),
                  'tunneled', wrapper is not None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    inspect(args.capture, args.session)
