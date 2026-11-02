"""List early App DUML frames and sequence numbers in a pcapng capture."""
import struct
import sys
from pcapng import FileScanner, blocks
from offline_takeoff import crc

target = int(sys.argv[2], 0)
shown = 0
with open(sys.argv[1], 'rb') as source:
    for block in FileScanner(source):
        if not isinstance(block, blocks.EnhancedPacket): continue
        data = block.packet_data
        if len(data)<42 or data[12:14]!=b'\x08\x00': continue
        ip=data[14:]
        if ip[9]!=17: continue
        offset=(ip[0]&15)*4
        if len(ip)<offset+8 or struct.unpack_from('!H',ip,offset+2)[0]!=9003: continue
        packet=ip[offset+8:]
        if len(packet)<34 or packet[6]!=4 or int.from_bytes(packet[2:4],'little')!=target: continue
        body=packet[34:]
        at=0
        while at+13<=len(body):
            if body[at]!=0x55:
                at+=1; continue
            size=body[at+1] | ((body[at+2]&3)<<8)
            frame=body[at:at+size]
            if size<13 or len(frame)!=size or crc(frame[:3],0x77,0x8c)!=frame[3] or crc(frame[:-2],0x3692,0x8408)!=int.from_bytes(frame[-2:],'little'):
                at+=1; continue
            print(round(block.timestamp,3),hex(int.from_bytes(frame[6:8],'little')),
                  f'{frame[4]:02x}>{frame[5]:02x}',f'{frame[9]:02x}/{frame[10]:02x}',size-13)
            shown+=1; at+=size
            if shown>=30: break
        if shown>=30: break
