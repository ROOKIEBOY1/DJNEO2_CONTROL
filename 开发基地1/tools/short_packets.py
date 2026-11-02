import collections
import glob
import struct
import sys
from pathlib import Path
from pcapng import FileScanner, blocks

for pattern in sys.argv[1:]:
    for path in glob.glob(pattern):
        counts=collections.Counter()
        with open(path,'rb') as src:
            for b in FileScanner(src):
                if not isinstance(b,blocks.EnhancedPacket): continue
                d=b.packet_data
                if len(d)<42 or d[12:14]!=b'\x08\x00': continue
                ip=d[14:]; off=(ip[0]&15)*4
                if ip[9]!=17 or len(ip)<off+8: continue
                sp,dp=struct.unpack_from('!HH',ip,off)
                if 9003 not in (sp,dp): continue
                p=ip[off+8:]
                if len(p)>=16 or len(p)<8: continue
                direction='from' if sp==9003 else 'to'
                length=int.from_bytes(p[:2],'little')&0x7fff
                xor=0
                for v in p[:7]: xor ^= v
                counts[(direction,len(p),length,p[6],xor==p[7],p[8:].hex())]+=1
        print(Path(path).name, counts.most_common(15))
