"""After a matching handshake, send bounded empty channel-4 packets only."""

import datetime
import json
import secrets
from pathlib import Path

from handshake_probe import DRONE_IP, DRONE_PORT, new_video_seed, probe_repeated
from run_handshake_offline import detect_local_ip


def main():
    local_ip = detect_local_ip()
    session = secrets.randbelow(0xffff) + 1
    video_seed = new_video_seed()
    result = probe_repeated(local_ip, DRONE_PORT, DRONE_IP, session, video_seed,
                            attempts=60, interval=0.55, after_match_seconds=5.0,
                            idle_seconds=2.0, idle_interval=0.06)
    record = {'local_ip': local_ip, 'session': f'0x{session:04x}',
              'video_seed': f'0x{video_seed:04x}', **result}
    captures = Path(__file__).resolve().parents[1] / 'captures'
    captures.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = captures / f'idle-link-{stamp}.json'
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved result: {output}')
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    main()
