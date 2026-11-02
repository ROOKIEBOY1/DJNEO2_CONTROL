"""Show DJI Fly DUML commands between handshake and first stick frame."""
import collections
import struct
import sys
from pcapng import FileScanner, blocks
from offline_takeoff import crc


def valid_frames(data):
    at = 0
    while at + 13 <= len(data):
        if data[at] != 0x55:
            at += 1
            continue
        length = data[at+1] | ((data[at+2] & 3) << 8)
        frame = data[at:at+length]
        if length < 13 or len(frame) != length or crc(frame[:3],0x77,0x8c) != frame[3] or crc(frame[:-2],0x3692,0x8408) != int.from_bytes(frame[-2:],'little'):
            at += 1
            continue
        yield frame
        at += length


session = int(sys.argv[2], 0)
handshake_at = None
first_stick_at = None
records = []
short_stick_samples = []
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
        if packet[6]==0 and len(packet)==48 and handshake_at is None:
            handshake_at=block.timestamp
        if packet[6]!=4 or len(packet)<34: continue
        for frame in valid_frames(packet[34:]):
            key=(frame[4],frame[5],frame[9],frame[10],len(frame)-13)
            if key==(2,169,1,10,28) and first_stick_at is None:
                first_stick_at=block.timestamp
            if key==(2,169,1,10,18) and len(short_stick_samples)<3:
                short_stick_samples.append(frame[11:-2].hex())
            records.append((block.timestamp,key))

if handshake_at is None or first_stick_at is None:
    raise SystemExit('Handshake or first stick not found')
counts=collections.Counter(key for t,key in records if handshake_at<=t<first_stick_at)
print('session',hex(session),'first stick after seconds',round(first_stick_at-handshake_at,3))
print('pre-stick frame count',sum(counts.values()))
print('early 18-byte 01/0a payload samples',short_stick_samples)
for key,count in counts.most_common(30):
    print(' '.join(f'{value:02x}' for value in key[:4]),'payload_len',key[4],'count',count)
