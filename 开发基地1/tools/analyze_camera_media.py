"""Read local pcapng: camera DUML evidence plus all IPv4 TCP/UDP endpoints.

HTTP entries are packet-local hints, not TCP reassembly or a file downloader.
No commands are sent. IPv6/fragmented traffic is counted as unsupported.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
from capture_messages import duml_messages


def ipv4_transport(frame):
    if len(frame) < 34 or frame[12:14] != b'\x08\x00':
        return None
    ip = frame[14:]
    size, total = (ip[0] & 15)*4, int.from_bytes(ip[2:4],'big')
    if ip[0] >> 4 != 4 or size < 20 or not size <= total <= len(ip):
        return None
    if int.from_bytes(ip[6:8],'big') & 0x3fff:
        return None
    segment = ip[size:total]
    if ip[9] == 6:
        if len(segment) < 20:
            return None
        offset = (segment[12] >> 4)*4
        if not 20 <= offset <= len(segment):
            return None
        protocol = 'TCP'
    elif ip[9] == 17:
        if len(segment) < 8:
            return None
        length = int.from_bytes(segment[4:6],'big')
        if not 8 <= length <= len(segment):
            return None
        segment, offset, protocol = segment[:length], 8, 'UDP'
    else:
        return None
    source_port, destination_port = struct.unpack_from('!HH',segment)
    address = lambda raw: '.'.join(str(value) for value in raw)
    return dict(protocol=protocol, source=f'{address(ip[12:16])}:{source_port}',
                destination=f'{address(ip[16:20])}:{destination_port}',
                source_port=source_port, destination_port=destination_port,
                payload=segment[offset:])


def http_request_hint(payload):
    match = re.match(rb'(GET|POST|HEAD|OPTIONS) ([^\r\n ]{1,2048}) HTTP/1\.[01]\r\n',payload)
    if match is None:
        return None
    return dict(method=match[1].decode('ascii'),
                path=match[2].split(b'?',1)[0].decode('utf-8',errors='replace')[:256])


def analyze(source):
    from pcapng import FileScanner, blocks
    endpoints, byte_counts, shapes = Counter(), Counter(), Counter()
    packets = skipped = 0
    camera_samples, http_hints = [], []
    first = last = None
    warning = None
    with Path(source).open('rb') as stream:
        try:
            for block in FileScanner(stream):
                if not isinstance(block, blocks.EnhancedPacket):
                    continue
                packets += 1
                first = block.timestamp if first is None else first
                last = block.timestamp
                interface = block.section.interfaces[block.interface_id]
                record = ipv4_transport(block.packet_data) if interface.link_type == 1 else None
                if record is None:
                    skipped += 1
                    continue
                key = (record['protocol'],record['source'],record['destination'])
                endpoints[key] += 1
                byte_counts[key] += len(record['payload'])
                if record['protocol'] == 'TCP' and len(http_hints) < 100:
                    hint = http_request_hint(record['payload'])
                    if hint:
                        http_hints.append(dict(hint, at=block.timestamp, source=key[1], destination=key[2]))
                if record['protocol'] != 'UDP' or 9003 not in (record['source_port'],record['destination_port']):
                    continue
                raw = record['payload']
                for frame, wrapper in duml_messages(raw):
                    if frame[9] not in (2,0x26):
                        continue
                    shape = f'{frame[4]:02x}>{frame[5]:02x} {frame[9]:02x}/{frame[10]:02x} flags={frame[8]:02x} len={len(frame)-13}'
                    shapes[shape] += 1
                    # Keep a bounded first sample per shape, including replies.
                    if shapes[shape] == 1 and len(camera_samples) < 150:
                        camera_samples.append(dict(at=block.timestamp, shape=shape,
                            sequence=int.from_bytes(frame[6:8],'little'), payload=frame[11:-2].hex(),
                            tunneled=wrapper is not None))
        except (EOFError, ValueError) as exc:
            warning = type(exc).__name__ + ': ' + str(exc)
    return dict(source=str(Path(source).resolve()), packets=packets, skipped_or_unsupported=skipped,
        start=first,end=last,read_warning=warning,
        endpoints=[dict(protocol=k[0], source=k[1], destination=k[2], packets=n, payload_bytes=byte_counts[k])
                   for k,n in endpoints.most_common(100)],
        camera_shapes=shapes.most_common(), camera_samples=camera_samples,
        http_request_hints=http_hints,
        limitations='Packet-local hints only; no IPv6/fragment reassembly, TCP reassembly, TLS decryption or original file extraction')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    output = args.output or args.source.with_suffix('.media-analysis.json')
    if output.exists():
        parser.error('分析结果已存在；请使用新的 --output 路径')
    report = analyze(args.source)
    with output.open('x',encoding='utf-8') as target:
        json.dump(report,target,ensure_ascii=False,indent=2)
    print('分析已保存：',output.resolve())
    print('packets:',report['packets'],'camera_shapes:',len(report['camera_shapes']),
          'endpoint_pairs:',len(report['endpoints']),'HTTP hints:',len(report['http_request_hints']))


if __name__ == '__main__':
    main()
