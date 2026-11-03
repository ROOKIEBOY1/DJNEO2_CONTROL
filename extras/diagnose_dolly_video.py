"""Offline-only dolly capture decoding experiments; does not open any sockets."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct

import av
from messages import udp_records
import video_stream

START = re.compile(b'\x00\x00(?:\x00)?\x01')

def nals(payload):
    starts = list(START.finditer(payload))
    for i, match in enumerate(starts):
        end = starts[i+1].start() if i+1 < len(starts) else len(payload)
        yield payload[match.start():end], payload[match.end():end]

def cache(source, dest):
    assembler = video_stream.VideoAssembler()
    records = udp_records(source) if source.suffix == '.pcapng' else (
        (r['time'], 'plane', bytes.fromhex(r['hex'])) for r in json.loads(source.read_text(encoding='utf-8'))['incoming'])
    count = 0
    original_clock = video_stream.time.monotonic
    try:
        with dest.open('wb') as out:
            for ts, side, raw in records:
                if side != 'plane' or len(raw) <= 20 or raw[6] != 2: continue
                video_stream.time.monotonic = lambda: ts
                payload = assembler.feed(raw)
                if payload is not None:
                    out.write(struct.pack('<dI', ts, len(payload))); out.write(payload)
                    count += 1
    finally:
        video_stream.time.monotonic = original_clock
    return dict(assembled=count, dropped=assembler.dropped_frames, duplicates=assembler.duplicate_packets)

def frames(path):
    with path.open('rb') as source:
        while header := source.read(12):
            ts, size = struct.unpack('<dI', header)
            yield ts, source.read(size)

def run(path, mode, jpeg=None):
    av.logging.set_level(av.logging.WARNING)
    codec = av.CodecContext.create('hevc', 'r'); codec.thread_count = 1
    filt = video_stream._TimingParameterFilter()
    stats = collections.Counter(); buckets = {}; events = []; params = {}; active = {}
    first = last = None; best_distance = float('inf')
    previous_output = None
    for index, (ts, payload) in enumerate(frames(path)):
        if first is None: first = ts
        elapsed = ts - first
        bucket = buckets.setdefault(int(elapsed // 10), collections.Counter())
        stats['assembled'] += 1; bucket['assembled'] += 1
        kinds = []
        for prefix, nal in nals(payload):
            if not nal: continue
            kind = (nal[0] >> 1) & 63; kinds.append(kind)
            if 32 <= kind <= 34:
                h = hashlib.sha256(nal).hexdigest()[:16]
                params[h] = dict(kind=kind, hex=nal.hex())
                if active.get(kind) != h:
                    events.append(dict(at=round(elapsed,3), event='parameter', kind=kind, hash=h))
                    active[kind] = h
        if mode == 'timing': payload = filt.feed(payload)
        elif mode == 'firstparams':
            pieces = []
            for prefix, nal in nals(payload):
                kind = (nal[0] >> 1) & 63
                key = 'seen_' + str(kind)
                if 32 <= kind <= 34:
                    if stats[key]: continue
                    stats[key] += 1
                pieces.append(prefix)
            payload = b''.join(pieces)
        if not payload: continue
        count = 0
        with av.logging.Capture(local=True) as messages:
            try:
                for packet in codec.parse(payload):
                    for frame in codec.decode(packet):
                        if frame.is_corrupt:
                            stats['corrupt'] += 1; continue
                        count += 1
                        if jpeg:
                            local = datetime.datetime.fromtimestamp(ts)
                            target = local.replace(hour=14,minute=30,second=46,microsecond=0).timestamp()
                            distance = abs(ts - target)
                            if distance < best_distance:
                                best_distance = distance
                                frame.to_image().save(jpeg)
            except Exception as e:
                stats['exceptions'] += 1
                if stats['exceptions'] < 5: events.append(dict(at=elapsed, exception=str(e)))
        if messages:
            stats['warnings'] += len(messages); bucket['warnings'] += len(messages)
            if len(events) < 100: events.append(dict(at=round(elapsed,3), warnings=[m[2].strip() for m in messages][:3], kinds=kinds))
        stats['decoded'] += count; bucket['decoded'] += count
        if count:
            last = elapsed
            if previous_output is not None and elapsed - previous_output > 1:
                events.append(dict(at=round(elapsed,3), event='resume', gap=round(elapsed-previous_output,3)))
            previous_output = elapsed
    return dict(mode=mode, stats=stats, duration=elapsed, last_decoded=last,
                suppressed=filt.suppressed, buckets=buckets, events=events, parameters=params,
                jpeg_target_distance=best_distance if jpeg else None)

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('source',type=Path); p.add_argument('--mode',default='timing'); p.add_argument('--jpeg',type=Path)
    args=p.parse_args(); cached=args.source.with_suffix('.assembled.bin')
    assembly=cache(args.source,cached) if not cached.exists() else {'cache':str(cached)}
    result=run(cached,args.mode,args.jpeg); result['assembly']=assembly
    output=args.source.with_suffix('.'+args.mode+'.diagnosis.json')
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),stats=result['stats'],duration=result['duration'],last_decoded=result['last_decoded'])))
