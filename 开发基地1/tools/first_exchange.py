import datetime
import struct
import sys
from pcapng import FileScanner, blocks

start = None
with open(sys.argv[1], 'rb') as src:
    for b in FileScanner(src):
        if not isinstance(b, blocks.EnhancedPacket): continue
        d=b.packet_data
        if len(d)<42 or d[12:14]!=b'\x08\x00': continue
        ip=d[14:]; off=(ip[0]&15)*4
        if ip[9]!=17 or len(ip)<off+8: continue
        sp,dp=struct.unpack_from('!HH',ip,off)
        if (sp,dp)!=(49800,9003) and (sp,dp)!=(9003,49800): continue
        if start is None: start=b.timestamp
        if b.timestamp-start>0.5: break
        p=ip[off+8:]
        print(round((b.timestamp-start)*1000,1),'ms', 'from' if sp==9003 else 'to', 'len',len(p),'head',p[:34].hex())
