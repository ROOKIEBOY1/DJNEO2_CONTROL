"""Compare short and full 01/0A frames around a DJI Fly connection."""
import collections
import struct
import sys
from pcapng import FileScanner, blocks
from offline_takeoff import crc

session = int(sys.argv[2], 0)
handshake = None
events = []
other_events = []
packet_samples = {}
packet_shapes = collections.defaultdict(collections.Counter)
with open(sys.argv[1], 'rb') as source:
    for block in FileScanner(source):
        if not isinstance(block, blocks.EnhancedPacket): continue
        data=block.packet_data
        if len(data)<42 or data[12:14]!=b'\x08\x00': continue
        ip=data[14:]
        if ip[9]!=17: continue
        offset=(ip[0]&15)*4
        if len(ip)<offset+8 or struct.unpack_from('!H',ip,offset+2)[0]!=9003: continue
        packet=ip[offset+8:]
        if len(packet)<16 or int.from_bytes(packet[2:4],'little')!=session: continue
        if packet[6]==0 and len(packet)==48 and handshake is None:
            handshake=block.timestamp
        if packet[6]!=4 or len(packet)<47: continue
        body=packet[34:]
        at=0
        while at+13<=len(body):
            if body[at]!=0x55: at+=1; continue
            size=body[at+1] | ((body[at+2]&3)<<8)
            frame=body[at:at+size]
            if size<13 or len(frame)!=size or crc(frame[:3],0x77,0x8c)!=frame[3] or crc(frame[:-2],0x3692,0x8408)!=int.from_bytes(frame[-2:],'little'):
                at+=1; continue
            if frame[4:6]==bytes.fromhex('02a9') and frame[9:11]==bytes.fromhex('010a'):
                length=size-13
                events.append((block.timestamp,length,int.from_bytes(frame[6:8],'little'),frame[11:-2].hex()))
                packet_samples.setdefault(length,packet.hex())
                packet_shapes[length][len(packet)] += 1
            else:
                other_events.append((block.timestamp,(frame[4],frame[5],frame[9],frame[10],size-13)))
            at+=size

if handshake is None: raise SystemExit('No handshake')
for length in (18,28):
    selected=[e for e in events if e[1]==length]
    if not selected: continue
    print('length',length,'count',len(selected),'first_s',round(selected[0][0]-handshake,3),
          'last_s',round(selected[-1][0]-handshake,3),
          'payload_variants',len(set(e[3] for e in selected)))
    print('first payload',selected[0][3])
first_full=next((e[0] for e in events if e[1]==28),None)
if first_full is not None:
    print('18B after first 28B',sum(e[1]==18 and e[0]>first_full for e in events))
    print('first 15 around transition')
    near=[e for e in events if first_full-0.5<=e[0]<=first_full+0.5]
    for t,length,seq,payload in near[:15]:
        print(round(t-handshake,3),length,hex(seq))
    for label, start, end in (
        ('before_first_short', first_full-2.5, first_full-2.0),
        ('before_first_full', first_full-0.5, first_full),
        ('after_first_full', first_full, first_full+0.5),
    ):
        counts=collections.Counter(key for t,key in other_events if start<=t<end)
        print(label,'other_frames',sum(counts.values()),'top',counts.most_common(8))
for length in (18,28):
    if length in packet_samples:
        packet=bytes.fromhex(packet_samples[length])
        print('packet_len',length,len(packet),'header',packet[:34].hex(),
              'frame_prefix',packet[34:45].hex())
        print('packet_shapes',length,sorted(packet_shapes[length].items()),'sample',packet.hex())
