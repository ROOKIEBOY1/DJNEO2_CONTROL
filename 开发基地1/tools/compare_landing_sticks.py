"""Compare owned DJI Fly stick stream around captured landing commands."""
from collections import Counter
from pathlib import Path
from capture_messages import duml_messages, udp_records

def compare(path):
    records = list(udp_records(path))
    frames = [(at, frame) for at, side, raw in records if side == 'app'
              for frame, _ in duml_messages(raw)]
    lands = [at for at, f in frames if f[9:11] == b'\x03\x2a' and f[11:-2] == b'\x02']
    print(path.name, 'landing commands', len(lands))
    for at in lands:
        print('landing', at)
        prior = None
        for t, side, raw in records:
            if side != 'plane' or not at-2 <= t <= at+20:
                continue
            for f, _ in duml_messages(raw):
                p = f[11:-2]
                if f[9:11] == b'\x03\x43' and len(p) >= 40:
                    state = (p[30]&127, p[31], p[32]&15, p[37])
                    if state != prior:
                        print('OSD transition', round(t-at,3), state, 'height', int.from_bytes(p[16:18], 'little', signed=True)/10)
                        prior = state
        for name, start, end in [('before', -2, 0), ('after', 0, 2), ('later', 2, 5)]:
            sticks = [(t-at, f[11:-2]) for t, f in frames if at+start <= t < at+end
                      and f[9:11] == b'\x01\x0a']
            shapes = Counter((len(p), p[:3].hex(), p[9:20].hex(), p[23:].hex()) for _, p in sticks)
            print(name, 'sticks',len(sticks), 'shapes', shapes.most_common(5),
                  'first',[(round(t,3),p.hex()) for t,p in sticks[:2]])

if __name__ == '__main__':
    base = Path(__file__).resolve().parents[1] / 'captures'
    for name in ('takeoff-forward-land-labeled-20261028.pcapng', 'takeoff-20261027.pcapng'):
        compare(base / name)
