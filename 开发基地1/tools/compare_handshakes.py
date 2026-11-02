import collections
import glob
import struct
import sys
from pathlib import Path
from pcapng import FileScanner, blocks

def packets(path):
    with open(path, 'rb') as f:
        header = f.read(4); f.seek(0)
        if header == bytes.fromhex('0a0d0d0a'):
            for b in FileScanner(f):
                if isinstance(b, blocks.EnhancedPacket): yield b.packet_data
        else:
            h=f.read(24)
            if len(h)<24: return
            endian = '<' if h[:4] in (bytes.fromhex('d4c3b2a1'),bytes.fromhex('4d3cb2a1')) else '>'
            if struct.unpack_from(endian+'I',h,20)[0] != 1: return
            while True:
                h=f.read(16)
                if len(h)<16: break
                size=struct.unpack_from(endian+'I',h,8)[0]
                d=f.read(size)
                if len(d)!=size: break
                yield d

for pattern in sys.argv[1:]:
    for file in glob.glob(pattern):
        found=collections.Counter(); sessions=collections.Counter()
        for d in packets(file):
            if len(d)<42 or d[12:14]!=b'\x08\x00': continue
            ip=d[14:]; off=(ip[0]&15)*4
            if ip[9]!=17 or len(ip)<off+8: continue
            sp,dp=struct.unpack_from('!HH',ip,off)
            if 9003 not in (sp,dp): continue
            p=ip[off+8:]
            if len(p)<16 or (int.from_bytes(p[:2],'little')&0x7fff)!=len(p): continue
            sess=int.from_bytes(p[2:4],'little'); sessions[sess]+=1
            if p[6]==0: found[(sp==9003,sess,p[8:16].hex(),p[16:].hex())]+=1
        print(Path(file).name,'sessions',sessions.most_common(4),'handshakes',found.most_common(4))
