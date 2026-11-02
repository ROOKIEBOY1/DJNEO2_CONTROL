import collections
import ipaddress
import struct
import sys
from pcapng import FileScanner, blocks

path=sys.argv[1]; target=float(sys.argv[2]); flows=collections.Counter(); first={}; last={}
with open(path,'rb') as src:
    for b in FileScanner(src):
        if not isinstance(b,blocks.EnhancedPacket) or not target-20 <= b.timestamp < target: continue
        d=b.packet_data
        if len(d)<34 or d[12:14]!=b'\x08\x00': continue
        ip=d[14:]; off=(ip[0]&15)*4
        if ip[9] not in (6,17) or len(ip)<off+4: continue
        a=str(ipaddress.ip_address(ip[12:16])); c=str(ipaddress.ip_address(ip[16:20]))
        sp,dp=struct.unpack_from('!HH',ip,off)
        if not (a.startswith('192.168.2.') or c.startswith('192.168.2.') or {sp,dp}=={67,68}): continue
        key=('tcp' if ip[9]==6 else 'udp',a,sp,c,dp)
        flows[key]+=1; first.setdefault(key, b.timestamp-target); last[key]=b.timestamp-target
for k,n in flows.most_common(25): print(k,n,round(first[k],3),round(last[k],3))
