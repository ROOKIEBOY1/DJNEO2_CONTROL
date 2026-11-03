"""Extract local JPEG proof from a saved pcapng; never connects to an aircraft."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
import json
from pathlib import Path

from messages import udp_records
from video_stream import HEVCDecoder, VideoAssembler


def decode_capture(capture, output, frames=5):
    if frames < 1:
        raise ValueError('frames must be positive')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    assembler = VideoAssembler()
    decoder = HEVCDecoder()
    decoded = assembled = errors = packets = 0
    for _, side, raw in udp_records(capture):
        if side != 'plane' or len(raw) <= 20 or raw[6] != 2:
            continue
        packets += 1
        payload = assembler.feed(raw)
        if payload is None:
            continue
        assembled += 1
        try:
            images = decoder.feed(payload)
        except Exception:
            errors += 1
            continue
        for image in images:
            decoded += 1
            (output / f'frame-{decoded:04d}.jpg').write_bytes(image)
            if decoded >= frames:
                return dict(decoded_frames=decoded, assembled_frames=assembled,
                            video_packets=packets, decode_errors=errors,
                            duplicate_packets=assembler.duplicate_packets,
                            dropped_frames=assembler.dropped_frames)
    return dict(decoded_frames=decoded, assembled_frames=assembled,
                video_packets=packets, decode_errors=errors,
                duplicate_packets=assembler.duplicate_packets,
                dropped_frames=assembler.dropped_frames)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=5)
    args = parser.parse_args()
    result = decode_capture(args.capture, args.output, args.frames)
    print(json.dumps(result, indent=2))
    return 0 if result['decoded_frames'] >= args.frames else 1


if __name__ == '__main__':
    raise SystemExit(main())
