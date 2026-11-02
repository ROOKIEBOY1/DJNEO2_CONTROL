"""Find prior occurrences of a session value in nearby unencrypted IP payloads."""
import argparse
import datetime
import ipaddress
import struct
from pcapng import FileScanner, blocks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('handshake_utc', help='ISO-8601 UTC timestamp')
    parser.add_argument('session_hex')
    args=parser.parse_args()
    event=datetime.datetime.fromisoformat(args.handshake_utc).timestamp()
    needle=int(args.session_hex,16).to_bytes(2,'little')
    matches=[]; inspected=0
    with open(args.capture,'rb') as src:
        for block in FileScanner(src):
            if not isinstance(block,blocks.EnhancedPacket) or not event-20 <= block.timestamp < event-0.001: continue
            data=block.packet_data
            if len(data)<34 or data[12:14]!=b'\x08\x00': continue
            ip=data[14:]; off=(ip[0]&15)*4
            if ip[9] not in (6,17) or len(ip)<off+8: continue
            source=str(ipaddress.ip_address(ip[12:16])); dest=str(ipaddress.ip_address(ip[16:20]))
            if not (source.startswith('192.168.2.') or dest.startswith('192.168.2.')): continue
            sp,dp=struct.unpack_from('!HH',ip,off)
            payload=ip[off+(8 if ip[9]==17 else (ip[off+12]>>4)*4):]
            inspected+=1
            if needle in payload:
                matches.append((round(block.timestamp-event,3),source,sp,dest,dp,len(payload)))
    print('inspected',inspected,'matches',matches)


if __name__=='__main__': main()
