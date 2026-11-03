"""Regression from real Follow HEVC bytes; no network or device access."""
from pathlib import Path
import unittest
from unittest.mock import patch
from diagnose_dolly_video import frames
from video_stream import HEVCDecoder, _LowDelayPictureOrder

FIXTURE=Path(__file__).resolve().parents[1]/'captures'/'follow-reordered-pair-fixture.assembled.bin'

class FollowVideoTests(unittest.TestCase):
    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_reordered_p_picture_pair_does_not_destroy_reference_chain(self):
        decoder=HEVCDecoder()
        decoded=sum(len(decoder.feed(payload)) for _,payload in frames(FIXTURE))
        self.assertEqual(decoded,6)
        self.assertEqual(decoder.corrupt_frames,0)
        self.assertLessEqual(decoder.warning_count,1)  # source IDR has one suffix-SEI warning
        self.assertEqual(decoder._picture_order.reordered,1)

    def order(self):
        order=_LowDelayPictureOrder()
        payloads=[p for _,p in frames(FIXTURE)]
        order.feed(payloads[0]);order.feed(payloads[1])
        return order

    def picture(self,poc,slice_type='010'):
        bits='11'+slice_type+f'{poc:04b}'+'1'
        bits+='0'*(-len(bits)%8)
        return b'\0\0\0\1\x02\x01'+int(bits,2).to_bytes(len(bits)//8,'big')

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_wrap_reorders_only_missing_predecessor(self):
        order=self.order()
        for poc in range(1,15):self.assertEqual(order.feed(self.picture(poc)),self.picture(poc))
        self.assertEqual(order.feed(self.picture(0)),b'')
        self.assertEqual(order.feed(self.picture(15)),self.picture(15)+self.picture(0))

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_real_loss_is_bounded_without_manufacturing_a_frame(self):
        order=self.order()
        self.assertEqual(order.feed(self.picture(2)),b'')
        self.assertEqual(order.feed(self.picture(3)),b'')
        self.assertEqual(order.feed(self.picture(4)),b''.join(self.picture(p) for p in (2,3,4)))
        self.assertFalse(order._pending)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_two_late_pictures_after_gap_release_cannot_rewind_sequence(self):
        order=self.order()
        with patch('video_stream.time.monotonic',return_value=0):
            for poc in (2,3,4):order.feed(self.picture(poc))
            self.assertEqual(order._expected,5)
            self.assertEqual(order.feed(self.picture(1)),b'')
            self.assertEqual(order.feed(self.picture(2)),b'')
            self.assertEqual(order.feed(self.picture(5)),self.picture(5))
            self.assertEqual(order.feed(self.picture(6)),self.picture(6))
        with patch('video_stream.time.monotonic',return_value=.13):
            self.assertEqual(order.feed(self.picture(7)),self.picture(7))
        self.assertEqual(order._expected,8)
        self.assertFalse(order._pending)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_large_gap_releases_before_half_range_can_alias_a_new_wrap(self):
        order=self.order()
        for poc in range(1,8):order.feed(self.picture(poc))
        self.assertEqual(order.feed(self.picture(14)),b'')
        self.assertEqual(order.feed(self.picture(15)),b'')
        self.assertEqual(order.feed(self.picture(0)),b''.join(self.picture(p) for p in (14,15,0)))
        self.assertEqual(order._expected,1)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_unknown_half_range_gap_passes_arrival_order_until_refresh(self):
        order=self.order()
        for poc in (14,15,0,1,3,2):
            self.assertEqual(order.feed(self.picture(poc)),self.picture(poc))
        self.assertFalse(order._pending)
        refresh=list(frames(FIXTURE))[1][1]
        self.assertEqual(order.feed(refresh),refresh)
        self.assertEqual(order.feed(self.picture(2)),b'')
        self.assertEqual(order.feed(self.picture(1)),self.picture(1)+self.picture(2))

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_b_picture_falls_back_in_arrival_order(self):
        order=self.order();pending=self.picture(2)
        self.assertEqual(order.feed(pending),b'')
        b_picture=self.picture(1,'1')
        self.assertEqual(order.feed(b_picture),pending+b_picture)
        self.assertEqual(order.feed(self.picture(3)),self.picture(3))

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_refresh_discards_old_pending_picture(self):
        order=self.order();order.feed(self.picture(2))
        refresh=list(frames(FIXTURE))[1][1]
        self.assertEqual(order.feed(refresh),refresh)
        self.assertFalse(order._pending)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_parameter_change_drops_pending_previous_sequence(self):
        order=self.order();order.feed(self.picture(2))
        # Same PPS id but output_flag_present changed: picture headers change.
        parameter=b'\0\0\0\1\x44\x01\xd1\x73\x12\x24\x08\x90'
        self.assertEqual(order.feed(parameter),parameter)
        self.assertFalse(order._pending)

if __name__=='__main__':unittest.main()
