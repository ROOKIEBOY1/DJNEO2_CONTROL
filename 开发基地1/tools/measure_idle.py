"""Measure DJI Fly's 34-byte App channel heartbeat intervals."""
import statistics
import struct
import sys
from pcapng import FileScanner, blocks

times=[]
with open(sys.argv[1], 'rb') as source:
    for block in FileScanner(source):
        if not isinstance(block, blocks.EnhancedPacket): continue
        data=block.packet_data
        if len(data)<42 or data[12:14]!=b'\x08\x00': continue
        ip=data[14:]
        if ip[9]!=17: continue
        offset=(ip[0]&15)*4
        if len(ip)<offset+8: continue
        source_port,dest_port=struct.unpack_from('!HH',ip,offset)
        if dest_port!=9003: continue
        packet=ip[offset+8:]
        if len(packet)==34 and packet[6]==4:
            times.append(block.timestamp)
gaps=[b-a for a,b in zip(times,times[1:]) if 0<b-a<1]
print('packets',len(times),'median gap ms',round(statistics.median(gaps)*1000,2) if gaps else None)
