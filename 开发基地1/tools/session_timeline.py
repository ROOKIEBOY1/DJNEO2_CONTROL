import collections
import datetime
import struct
import sys
from pcapng import FileScanner, blocks

sessions = collections.Counter()
last = None
events = []
firsts = {}
with open(sys.argv[1], 'rb') as src:
    for block in FileScanner(src):
        if not isinstance(block, blocks.EnhancedPacket): continue
        d = block.packet_data
        if len(d) < 42 or d[12:14] != b'\x08\x00': continue
        ip = d[14:]; off = (ip[0] & 15)*4
        if ip[9] != 17 or len(ip) < off+8: continue
        sp, dp = struct.unpack_from('!HH', ip, off)
        if 9003 not in (sp,dp): continue
        p = ip[off+8:]
        if len(p)<16 or (int.from_bytes(p[:2], 'little') & 0x7fff) != len(p): continue
        t = block.timestamp; direction = 'from' if sp == 9003 else 'to'; session = int.from_bytes(p[2:4],'little')
        sessions[(session,direction,p[6])] += 1
        key = (session,direction,p[6])
        if key not in firsts:
            firsts[key] = (datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(), len(p), p[:48].hex())
        if last is None or t-last > 2 or p[6] == 0:
            events.append((datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(), round(t-last,2) if last else None, direction, hex(session), hex(p[6]), len(p), p[:48].hex()))
        last = t
print('sessions:', sessions)
print('first packets by session and channel:')
for key, event in firsts.items(): print(key, event)
for event in events: print(event)
