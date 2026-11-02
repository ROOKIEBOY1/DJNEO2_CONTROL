"""Summarize handshake exchanges in a saved pcapng file; never sends packets."""

import argparse
import datetime
import json
import struct
from collections import Counter
from pcapng import FileScanner, blocks

from capture_protocol import parse_outer


def analyze(path):
    handshakes = []
    replies = []
    sessions = Counter()
    malformed = 0
    with open(path, 'rb') as src:
        for block in FileScanner(src):
            if not isinstance(block, blocks.EnhancedPacket):
                continue
            ethernet = block.packet_data
            if len(ethernet) < 42 or ethernet[12:14] != b'\x08\x00':
                continue
            ip = ethernet[14:]
            offset = (ip[0] & 15) * 4
            if ip[9] != 17 or len(ip) < offset + 8:
                continue
            source_port, dest_port = struct.unpack_from('!HH', ip, offset)
            if 9003 not in (source_port, dest_port):
                continue
            raw = ip[offset + 8:]
            try:
                packet = parse_outer(raw)
            except ValueError:
                malformed += 1
                continue
            direction = 'from' if source_port == 9003 else 'to'
            sessions[(packet.session, direction, packet.channel)] += 1
            if direction == 'to' and packet.channel == 0 and packet.header_length == 16 and len(packet.body) == 32:
                handshakes.append((block.timestamp, packet.session, packet.body.hex()))
            if direction == 'from' and packet.channel == 0 and packet.header_length == 8:
                replies.append((block.timestamp, packet.session, packet.body.hex()))
    exchanges = []
    for timestamp, session, body in handshakes:
        following = [reply for reply in replies if reply[1] == session and 0 <= reply[0] - timestamp < 1]
        exchanges.append({
            'session': f'0x{session:04x}',
            'handshake_body': body,
            'short_reply_ms': round((following[0][0] - timestamp) * 1000, 1) if following else None,
            'short_reply_body': following[0][2] if following else None,
        })
    return {
        'handshakes': exchanges,
        'handshake_count': len(handshakes),
        'short_reply_count': len(replies),
        'first_handshake_utc': datetime.datetime.fromtimestamp(handshakes[0][0], datetime.timezone.utc).isoformat() if handshakes else None,
        'last_handshake_utc': datetime.datetime.fromtimestamp(handshakes[-1][0], datetime.timezone.utc).isoformat() if handshakes else None,
        'sessions': {f'0x{session:04x}/{direction}/{channel}': count for (session, direction, channel), count in sessions.items()},
        'malformed_udp_9003': malformed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    args = parser.parse_args()
    result = analyze(args.capture)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
