"""Summarize a Windows pktmon pcapng without assuming a 14-byte Ethernet header."""
import collections
import ipaddress
import struct
import sys
from pcapng import FileScanner, blocks

flows = collections.Counter()
with open(sys.argv[1], 'rb') as source:
    for block in FileScanner(source):
        if not isinstance(block, blocks.EnhancedPacket):
            continue
        data = block.packet_data
        # pktmon's NIC capture can include an 802.11/SNAP prefix even when
        # its pcapng interface reports Ethernet. Locate an IPv4 header.
        ip_offset = data.find(b'\x45\x00')
        if ip_offset < 0 or len(data) < ip_offset + 20:
            continue
        ip = data[ip_offset:]
        source_ip = str(ipaddress.ip_address(ip[12:16]))
        dest_ip = str(ipaddress.ip_address(ip[16:20]))
        protocol = ip[9]
        header_len = (ip[0] & 15) * 4
        if protocol in (6, 17) and len(ip) >= header_len + 4:
            ports = struct.unpack_from('!HH', ip, header_len)
        else:
            ports = None
        flows[(source_ip, dest_ip, protocol, ports)] += 1

for flow, count in flows.most_common():
    print(count, flow)
