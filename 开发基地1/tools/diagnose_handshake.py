"""Capture NIC packets while running a bounded handshake-only experiment.

Requires an elevated Windows terminal for pktmon. Captures may contain other
network traffic, so keep the resulting pcapng private until filtered.
"""

import ctypes
import datetime
import json
import secrets
import subprocess
import sys
from pathlib import Path

from handshake_probe import DRONE_IP, DRONE_PORT, new_video_seed, probe_repeated
from run_handshake_offline import detect_local_ip


def run_checked(*args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        raise RuntimeError(f"{' '.join(args)} failed: {stdout} {stderr}")


def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        script = str(Path(__file__).resolve())
        params = subprocess.list2cmdline([script, '--elevated-window'])
        code = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable,
                                                   params, str(Path(script).parent), 1)
        if code <= 32:
            raise SystemExit(f'Administrator request was cancelled or failed (code {code}).')
        print('Administrator approval requested. The diagnostic will run in a new window.')
        return
    local_ip = detect_local_ip()
    captures = Path(__file__).resolve().parents[1] / 'captures'
    captures.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    etl = captures / f'handshake-diagnostic-{stamp}.etl'
    pcap = captures / f'handshake-diagnostic-{stamp}.pcapng'
    summary = captures / f'handshake-diagnostic-{stamp}.json'
    session = secrets.randbelow(0xffff) + 1
    ack_word = new_video_seed()
    print('Recording Wi-Fi traffic and testing handshake for about 33 seconds...', flush=True)
    run_checked('pktmon', 'start', '--capture', '--comp', 'nics', '--pkt-size', '0', '--file-name', str(etl))
    try:
        result = probe_repeated(local_ip, DRONE_PORT, DRONE_IP, session, ack_word,
                                attempts=60, interval=0.55, after_match_seconds=2.0)
    finally:
        run_checked('pktmon', 'stop')
    run_checked('pktmon', 'etl2pcap', str(etl), '--out', str(pcap))
    record = {'local_ip': local_ip, 'session': f'0x{session:04x}',
              'ack_word': f'0x{ack_word:04x}', **result}
    summary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Summary:', summary)
    print('Packet capture:', pcap)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    finally:
        if '--elevated-window' in sys.argv:
            input('Press Enter to close this window after noting the saved file paths...')
