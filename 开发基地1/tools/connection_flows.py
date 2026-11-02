import collections
import datetime
import ipaddress
import struct
import sys
from pcapng import FileScanner, blocks

flows = collections.Counter(); first = {}; last = {}
with open(sys.argv[1], 'rb') as src:
    for block in FileScanner(src):
        if not isinstance(block, blocks.EnhancedPacket): continue
        d = block.packet_data
        if len(d)<34 or d[12:14]!=b'\x08\x00': continue
        ip=d[14:]; ihl=(ip[0]&15)*4
        if ip[9] not in (6,17) or len(ip)<ihl+4: continue
        a=str(ipaddress.ip_address(ip[12:16])); b=str(ipaddress.ip_address(ip[16:20]))
        if '192.168.2.1' not in (a,b): continue
        sp,dp=struct.unpack_from('!HH',ip,ihl)
        key=('TCP' if ip[9]==6 else 'UDP',a,sp,b,dp)
        flows[key]+=1
        first.setdefault(key,block.timestamp); last[key]=block.timestamp
for k,n in flows.most_common():
    fmt=lambda v: datetime.datetime.fromtimestamp(v,datetime.timezone.utc).isoformat()
    print(k, n, fmt(first[k]), fmt(last[k]))
