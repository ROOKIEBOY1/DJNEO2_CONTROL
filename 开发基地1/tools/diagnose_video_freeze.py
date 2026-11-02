"""Receive-only replay of a complete local capture; no aircraft I/O."""
import argparse
import collections
import json
from unittest.mock import patch

import av
from capture_messages import udp_records
from video_stream import HEVCDecoder, VideoAssembler


def run(path, original=False):
    assembler = VideoAssembler()
    decoder = HEVCDecoder()
    if original:
        class Identity:
            suppressed = 0
            def feed(self, payload): return payload
        decoder._parameters = Identity()
    stats = collections.Counter()
    buckets = {}
    start = last = previous_frame = None
    duration = 0
    irap_times = []
    gaps = []
    for ts, side, raw in udp_records(path):
        if side != 'plane' or len(raw) <= 20 or raw[6] != 2:
            continue
        if start is None: start = ts
        elapsed = ts - start
        duration = max(duration, elapsed)
        bucket = buckets.setdefault(int(elapsed // 10), collections.Counter())
        stats['packets'] += 1
        bucket['packets'] += 1
        with patch('video_stream.time.monotonic', return_value=ts):
            payload = assembler.feed(raw)
        if payload is None: continue
        stats['assembled'] += 1
        bucket['assembled'] += 1
        frame_id = raw[16]
        if previous_frame is not None and (frame_id - previous_frame) % 256 > 1 and len(gaps) < 20:
            gaps.append(dict(time=round(elapsed, 3), previous=previous_frame, current=frame_id,
                             last_outer_sequence=int.from_bytes(raw[4:6], 'little')))
        previous_frame = frame_id
        import re
        for match in re.finditer(b'\x00\x00\x01', payload):
            if match.end() < len(payload):
                kind = (payload[match.end()] >> 1) & 63
                stats['nal_' + str(kind)] += 1
                if 16 <= kind <= 23: irap_times.append(round(elapsed, 3))
        try:
            images = decoder.feed(payload)
            stats['decoded'] += len(images)
            bucket['decoded'] += len(images)
            if images: last = elapsed
        except Exception:
            stats['exceptions'] += 1
            bucket['exceptions'] += 1
    return dict(file=str(path), mode='original' if original else 'timing-filter',
                duration=duration, last_decode=last, stats=stats, buckets=buckets, irap_nal_times=irap_times,
                decoder_warnings=decoder.warning_count, last_decoder_warning=decoder.last_warning,
                corrupt_frames_rejected=decoder.corrupt_frames,
                timing_parameters_suppressed=decoder._parameters.suppressed,
                dropped=assembler.dropped_frames, duplicate=assembler.duplicate_packets,
                invalid=assembler.invalid_packets, first_frame_id_gaps=gaps)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--original', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.capture, args.original), indent=2))
