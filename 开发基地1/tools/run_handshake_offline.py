"""Run one bounded handshake experiment and save its result for later review."""

import datetime
import json
import socket
from pathlib import Path

from handshake_probe import DRONE_IP, DRONE_PORT, new_video_seed, probe_repeated, validate_drone_local_ip


def detect_local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((DRONE_IP, DRONE_PORT))  # Select a route; sends no packet.
        return validate_drone_local_ip(sock.getsockname()[0])


def main():
    local_ip = detect_local_ip()
    import secrets
    session = secrets.randbelow(0xffff) + 1
    ack_word = new_video_seed()
    result = probe_repeated(local_ip, DRONE_PORT, DRONE_IP, session, ack_word,
                            attempts=60, interval=0.55, after_match_seconds=2.0)
    record = {
        'local_ip': local_ip,
        'session': f'0x{session:04x}',
        'ack_word': f'0x{ack_word:04x}',
        **result,
    }
    captures = Path(__file__).resolve().parents[1] / 'captures'
    captures.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = captures / f'handshake-probe-{stamp}.json'
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved result: {output}')
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    main()
