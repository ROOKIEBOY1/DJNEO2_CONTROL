"""Extract a small, local evidence fixture from the user's labeled captures."""
import json
from pathlib import Path
from capture_messages import duml_messages, udp_records

BASE = Path(__file__).resolve().parents[1]
SOURCES = {
    'circle-20261029.pcapng': {
        'radius_write_2m': (2, 0x92, 3, 0xf9, 52134),
        'radius_write_ack': (0x92, 2, 3, 0xf9, 52134),
        'circle_selection': (2, 0x17, 3, 0xf9, 51362),
    },
    'circle-20261029-actions.pcapng': {
        'start_first': (2, 0x92, 0x23, 0x13, 36190),
        'start_first_ack': (0x92, 2, 0x23, 0x13, 36190),
        'start_second': (2, 0x92, 0x23, 0x13, 38935),
        'start_second_ack': (0x92, 2, 0x23, 0x13, 38935),
    },
    'circle-stop-20261029-172351.pcapng': {
        'stop_trial_start': (2, 0x92, 0x23, 0x13, 38577),
        'stop_trial_start_ack': (0x92, 2, 0x23, 0x13, 38577),
        'stop_request': (2, 0x92, 0x23, 0x15, 39638),
        'stop_ack': (0x92, 2, 0x23, 0x15, 39638),
    },
}

def main():
    result = {'operator_labels': {'radius_before_m': 10, 'radius_after_m': 2,
                                   'circle_runs': 2, 'manual_stop': False,
                                   'completion': 'both_automatic'},
              'stop_trial_operator_label': 'clicked_stop_and_aircraft_hovered', 'evidence': {}}
    for name, wanted in SOURCES.items():
        remaining = dict(wanted)
        for at, side, raw in udp_records(BASE / 'captures' / name):
            for frame, wrapper in duml_messages(raw):
                key = frame[4], frame[5], frame[9], frame[10], int.from_bytes(frame[6:8], 'little')
                for label, expected in list(remaining.items()):
                    if key == expected:
                        result['evidence'][label] = {'source': name, 'at': at, 'side': side,
                            'session': int.from_bytes(raw[2:4], 'little'), 'frame_hex': frame.hex(),
                            'tunneled': wrapper is not None}
                        del remaining[label]
            if not remaining:
                break
        if remaining:
            raise ValueError(f'Missing evidence: {remaining}')
    output = BASE / 'profiles' / 'neo2-circle-evidence.json'
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('Extracted', len(result['evidence']), 'original frames to', output)

if __name__ == '__main__':
    main()
