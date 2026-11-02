import collections
import ipaddress
import struct
import sys
from pcapng import FileScanner, blocks

counts = collections.Counter()
directions = collections.Counter()
channels = collections.Counter()
sessions = collections.Counter()
total = 0
with open(sys.argv[1], 'rb') as source:
    for block in FileScanner(source):
        if not isinstance(block, blocks.EnhancedPacket):
            continue
        total += 1
        data = block.packet_data
        if len(data) < 42 or data[12:14] != b'\x08\x00':
            continue
        ip = data[14:]
        if ip[9] != 17:
            continue
        off = (ip[0] & 15) * 4
        if len(ip) < off + 8:
            continue
        src, dst = str(ipaddress.ip_address(ip[12:16])), str(ipaddress.ip_address(ip[16:20]))
        sp, dp = struct.unpack_from('!HH', ip, off)
        if sp != 9003 and dp != 9003:
            continue
        counts['udp9003'] += 1
        directions[(src, dst)] += 1
        body = ip[off + 8:]
        if len(body) >= 16 and (int.from_bytes(body[:2], 'little') & 0x7fff) == len(body):
            channels[body[6]] += 1
            sessions[int.from_bytes(body[2:4], 'little')] += 1
print('packets:', total)
print('udp9003:', counts['udp9003'])
print('directions:', dict(directions))
print('channels:', dict(channels))
print('sessions:', dict(sessions))
