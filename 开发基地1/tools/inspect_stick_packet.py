"""Print a few captured single-frame neutral stick packets for offline comparison."""
import struct
import sys
from pcapng import FileScanner, blocks

shown=0
session_filter = int(sys.argv[2], 0) if len(sys.argv) > 2 else None
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
        if len(packet)!=75 or packet[6]!=4 or packet[34:37]!=bytes.fromhex('552904'):
            continue
        if session_filter is not None and int.from_bytes(packet[2:4], 'little') != session_filter:
            continue
        frame=packet[34:]
        if frame[4:6]!=bytes.fromhex('02a9') or frame[9:11]!=bytes.fromhex('010a'):
            continue
        print('time',block.timestamp,'session',hex(int.from_bytes(packet[2:4],'little')),
              'sequence',hex(int.from_bytes(frame[6:8],'little')),
              'counter',hex(int.from_bytes(frame[31:34],'little')))
        print(packet.hex())
        shown+=1
        if shown>=3: break
