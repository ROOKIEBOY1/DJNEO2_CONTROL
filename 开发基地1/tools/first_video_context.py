"""Compare captured app traffic immediately before the first video packet."""

import argparse
import collections
import struct

from pcapng import FileScanner, blocks
from transition_events import read_events


def first_video(path, session):
    with open(path, 'rb') as source:
        for block in FileScanner(source):
            if not isinstance(block, blocks.EnhancedPacket):
                continue
            data = block.packet_data
            if len(data) < 42 or data[12:14] != b'\x08\x00':
                continue
            ip = data[14:]
            if ip[9] != 17:
                continue
            offset = (ip[0] & 15) * 4
            if len(ip) < offset + 8:
                continue
            src_port = struct.unpack_from('!H', ip, offset)[0]
            if src_port != 9003:
                continue
            packet = ip[offset + 8:]
            if (len(packet) >= 16 and packet[6] == 2 and
                    int.from_bytes(packet[2:4], 'little') == session):
                return block.timestamp
    raise ValueError('No video in selected session')


def summarize(path, session):
    handshake, events = read_events(path, session)
    video = first_video(path, session)
    print('session', f'0x{session:04x}', 'first_video_after_handshake_s',
          round(video - handshake, 3))
    for command_id in (0x41, 0x13):
        matches = [e for e in events if e[1] == 'app' and e[2:6] ==
                   (0xee, 0xe9, 0x51, command_id) and e[0] < video]
        print(f'ee>e9 51/{command_id:02x} before video', len(matches),
              'last_ms', round((matches[-1][0] - video) * 1000)
              if matches else None)
    for seconds in (0.5, 0.1):
        rows = [e for e in events if e[1] == 'app'
                and video - seconds <= e[0] < video]
        shapes = collections.Counter((e[2], e[3], e[4], e[5], e[6]) for e in rows)
        print('before', seconds, 'app_frames', len(rows), 'shapes', shapes.most_common(15))
        if seconds == 0.1:
            for e in rows[-15:]:
                print('  ms', round((e[0] - video) * 1000),
                      f'{e[2]:02x}>{e[3]:02x} {e[4]:02x}/{e[5]:02x}',
                      'length', e[6], 'payload_prefix', e[8][:20])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('session', type=lambda value: int(value, 0))
    args = parser.parse_args()
    summarize(args.capture, args.session)
