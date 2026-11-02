import struct
from pathlib import Path
import unittest
from unittest.mock import patch
from offline_takeoff import encode_frame
from reliable_transport import header
from media_directory_link import DirectoryStream


def frame(session=2):
    body=b'data'
    payload=b'\x4a\x01'+struct.pack('<HHI',0x1000+10+len(body),session,0)+body
    return encode_frame(0xe1,2,0,0,0,0x27,payload)


def packet(sequence,data,outer_session=1):
    return bytes(header(outer_session,3,20+len(data),sequence))+bytes(4)+data


class DirectoryStreamTests(unittest.TestCase):
    def test_real_seven_packet_response_reorders_to_latest_originals(self):
        from capture_messages import udp_records
        path=Path(__file__).resolve().parents[1]/'captures/camera-media-20261030-141959.pcapng'
        packets=[raw for at,side,raw in udp_records(path)
                 if side=='plane' and raw[6]==3 and 1793341283.5<at<1793341283.6]
        self.assertEqual(len(packets),7)
        receiver=DirectoryStream(outer_session=int.from_bytes(packets[0][2:4],'little'),
            file_session=2,first_sequence=int.from_bytes(packets[0][4:6],'little'))
        for raw in packets[::-1]:receiver.feed(raw)
        self.assertIsNone(receiver.error)
        self.assertTrue(receiver.done.is_set())
        self.assertEqual(len(receiver.entries),34)
        self.assertEqual(receiver.entries[0]['size'],5636096)
        self.assertEqual(receiver.entries[1]['size'],61317634)
    def test_out_of_order_and_duplicates_are_reassembled_before_parsing(self):
        receiver=DirectoryStream(outer_session=1,file_session=2,first_sequence=8)
        data=frame()
        with patch('media_directory_link.parse_directory_frames',return_value=[{'name':'a.jpg'}]) as parse:
            receiver.feed(packet(16,data[7:]))
            receiver.feed(packet(16,data[7:]))
            self.assertFalse(receiver.done.is_set())
            receiver.feed(packet(8,data[:7]))
            self.assertTrue(receiver.done.is_set())
            self.assertEqual(receiver.entries,[{'name':'a.jpg'}])
            parse.assert_called_once_with([data],session_id=2)

    def test_corruption_and_future_window_are_reported(self):
        for data,sequence in ((bytearray(frame()),8),(b'future',8+8*129)):
            receiver=DirectoryStream(outer_session=1,file_session=2,first_sequence=8)
            if sequence==8:data[-1]^=1
            receiver.feed(packet(sequence,bytes(data)))
            self.assertTrue(receiver.done.is_set())
            self.assertIsNotNone(receiver.error)
            self.assertIsNone(receiver.entries)

    def test_old_transfer_and_wrong_outer_session_do_not_complete_new_request(self):
        receiver=DirectoryStream(outer_session=1,file_session=2,first_sequence=8)
        receiver.feed(packet(8,frame(),outer_session=9))
        self.assertFalse(receiver.done.is_set())
        receiver.feed(packet(8,frame(session=3)))
        self.assertFalse(receiver.done.is_set())
        with patch('media_directory_link.parse_directory_frames',return_value=[]):
            receiver.feed(packet(16,frame()))
            self.assertTrue(receiver.done.is_set())

    def test_conflicting_out_of_order_duplicate_is_not_accepted(self):
        receiver=DirectoryStream(outer_session=1,file_session=2,first_sequence=8)
        receiver.feed(packet(16,b'a'))
        receiver.feed(packet(16,b'b'))
        self.assertIsNotNone(receiver.error)
