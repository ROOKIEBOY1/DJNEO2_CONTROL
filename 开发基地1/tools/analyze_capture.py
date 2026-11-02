import collections
import datetime
import ipaddress
import statistics
import struct
import sys
from pcapng import FileScanner, blocks

def crc(data, initial, polynomial):
    c = initial
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (polynomial if c & 1 else 0)
    return c

def frames(data):
    i = 0
    while i + 13 <= len(data):
        if data[i] != 0x55:
            i += 1
            continue
        n = data[i+1] | ((data[i+2] & 3) << 8)
        if data[i+2] >> 2 != 1 or n < 13 or i+n > len(data) or crc(data[i:i+3], 0x77, 0x8c) != data[i+3]:
            i += 1
            continue
        f = data[i:i+n]
        if crc(f[:-2], 0x3692, 0x8408) != int.from_bytes(f[-2:], 'little'):
            i += 1
            continue
        yield f
        i += n

channels = collections.Counter(); commands = collections.Counter(); handshake = collections.Counter()
stickvalues = [collections.Counter() for _ in range(4)]; sticks = []; times = []
invalid = 0; prefixes = collections.Counter(); stick_samples = []; counters = []; nested = collections.Counter(); xor_bad = 0
with open(sys.argv[1], 'rb') as src:
    for block in FileScanner(src):
        if not isinstance(block, blocks.EnhancedPacket): continue
        d = block.packet_data
        if len(d) < 42 or d[12:14] != b'\x08\x00': continue
        ip = d[14:]; off = (ip[0] & 15)*4
        if ip[9] != 17 or len(ip) < off+8: continue
        sp, dp = struct.unpack_from('!HH', ip, off)
        if 9003 not in (sp, dp): continue
        p = ip[off+8:]
        if len(p)<16 or int.from_bytes(p[:2], 'little') & 0x7fff != len(p):
            invalid += 1; continue
        direction = 'from_drone' if sp == 9003 else 'to_drone'
        xor_bad += (p[0] ^ p[1] ^ p[2] ^ p[3] ^ p[4] ^ p[5] ^ p[6]) != p[7]
        t = block.timestamp; times.append(t); ch = p[6]
        channels[(direction, ch)] += 1
        if ch == 0 and sum(handshake.values()) < 100: handshake[(direction, p[16:].hex())] += 1
        if ch not in (1,4): continue
        prefixes[p[16:32].hex()] += 1
        for f in frames(p[16:]):
            commands[(direction, f[4],f[5],f[9],f[10],f[8],len(f)-13)] += 1
            if f[9:11] == bytes([0x51,1]):
                for sub in frames(f[11:-2]):
                    nested[(direction, sub[4],sub[5],sub[9],sub[10],sub[11:-2].hex())] += 1
                    if sub[9:11] == bytes([3,42]):
                        print('FLY', datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(), direction,
                              'outer_udp',p.hex(), 'nested',sub.hex())
            if direction == 'to_drone' and f[4:6] == bytes([2,169]) and f[9:11] == bytes([1,10]) and len(f)==41:
                payload = f[11:-2]; bits = int.from_bytes(payload, 'little')
                vals = tuple((bits >> (24+11*k)) & 2047 for k in range(4))
                for k,v in enumerate(vals): stickvalues[k][v] += 1
                sticks.append(t)
                counters.append(int.from_bytes(payload[20:23], 'little'))
                if len(stick_samples)<3: stick_samples.append(payload.hex())
print('first UTC:',datetime.datetime.fromtimestamp(min(times),datetime.timezone.utc).isoformat())
print('last UTC:',datetime.datetime.fromtimestamp(max(times),datetime.timezone.utc).isoformat())
print('duration:',round(max(times)-min(times),2),'invalid:',invalid,'bad xor:',xor_bad)
print('channels:',channels)
print('handshakes:',handshake.most_common(6))
print('prefixes:',prefixes.most_common(3))
print('commands:')
for key,n in commands.most_common(30): print(key,n)
print('stick count:',len(sticks),'samples:',stick_samples)
print('nested fly commands:', [(k,n) for k,n in nested.items() if k[3:5] == (3,42)])
print('stick counter delta top:', collections.Counter((b-a)&0xffffff for a,b in zip(counters,counters[1:])).most_common(8))
for i,c in enumerate(stickvalues): print('axis',i,'range',min(c) if c else None,max(c) if c else None,'top',c.most_common(5))
if len(sticks)>1:
    gaps=[b-a for a,b in zip(sticks,sticks[1:]) if b>=a]
    print('stick gaps median / p90 / max ms:',round(statistics.median(gaps)*1000,2),round(sorted(gaps)[int(len(gaps)*.9)]*1000,2),round(max(gaps)*1000,2))
