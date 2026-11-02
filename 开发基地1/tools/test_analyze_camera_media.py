import struct
import unittest
from analyze_camera_media import ipv4_transport, http_request_hint


def ethernet(payload, protocol=6, fragment=0):
    tcp = struct.pack('!HHIIHHHH', 1234, 80, 1, 0, 5 << 12, 1000, 0, 0) + payload
    ip = bytearray(20)
    ip[0] = 0x45
    ip[2:4] = (20 + len(tcp)).to_bytes(2,'big')
    ip[6:8] = fragment.to_bytes(2,'big')
    ip[9] = protocol
    ip[12:16] = bytes((192,168,2,156))
    ip[16:20] = bytes((192,168,2,1))
    return bytes(12) + b'\x08\x00' + ip + tcp


class MediaAnalysisTests(unittest.TestCase):
    def test_preserves_tcp_payload_and_ports_outside_control_channel(self):
        record = ipv4_transport(ethernet(b'GET /DCIM/test.JPG HTTP/1.1\r\n'))
        self.assertEqual(record['destination'], '192.168.2.1:80')
        self.assertEqual(record['protocol'], 'TCP')
        self.assertEqual(http_request_hint(record['payload']), {'method':'GET','path':'/DCIM/test.JPG'})

    def test_truncated_and_ip_fragments_are_not_misread_as_ports(self):
        for frame in (b'', ethernet(b'')[:40], ethernet(b'x',fragment=1), ethernet(b'x',fragment=0x2000)):
            self.assertIsNone(ipv4_transport(frame))

    def test_http_hint_omits_queries_and_headers(self):
        hint = http_request_hint(b'GET /photo.JPG?token=secret HTTP/1.1\r\nAuthorization: secret\r\n')
        self.assertEqual(hint, {'method':'GET','path':'/photo.JPG'})
        self.assertIsNone(http_request_hint(b'not http'))
