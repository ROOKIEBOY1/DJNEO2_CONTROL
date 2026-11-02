import math
import struct
import unittest
from duml import encode_frame
from subject_observation import (parse_detection, parse_tracking_box,
                                 parse_task_state, SubjectObservation)

EMPTY = bytes.fromhex('02b2760a0000000000010400')
SINGLE = bytes.fromhex('0224f20a00000b00000104010b0002017515ca123f078f11')
TRACKED = bytes.fromhex('010b017c210b3f3808f63e0a403d3e398de53e01')

def frame(ci, payload, cs=10, sender=0x92, receiver=2):
    return encode_frame(sender, receiver, 5, 0, cs, ci, payload)

class SubjectObservationTests(unittest.TestCase):
    def test_actual_detection_formats_and_unsigned_coordinates(self):
        self.assertEqual(parse_detection(EMPTY)['targets'], [])
        detection = parse_detection(SINGLE)
        self.assertEqual(detection['count'], 1)
        target = detection['targets'][0]
        self.assertEqual((target['id'], target['class_raw'], target['state_raw']), (11, 2, 1))
        self.assertEqual(target['bbox'], dict(x=.5493, y=.481, width=.1855, height=.4495))

    def test_float_box_agrees_with_independent_detection_stream(self):
        target = parse_tracking_box(TRACKED)['targets'][0]
        self.assertEqual(target['id'], 11)
        detected = parse_detection(SINGLE)['targets'][0]
        for field in ('x', 'y', 'width', 'height'):
            self.assertLess(abs(target['bbox'][field] - detected['bbox'][field]), .01)
        self.assertEqual(parse_tracking_box(b'\x00')['targets'], [])

    def test_unsupported_versions_lengths_and_nonfinite_boxes_are_rejected(self):
        for invalid in (SINGLE[:-1], EMPTY+b'\x00', b'\x03'+EMPTY[1:], EMPTY[:11]+b'\x02'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_detection(invalid)
        bad = bytearray(TRACKED)
        bad[3:7] = struct.pack('<f', math.nan)
        for invalid in (TRACKED[:-1], bytes(bad), b'\x01'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_tracking_box(invalid)

    def test_observation_exposes_candidates_but_never_claims_verified_lock(self):
        monitor = SubjectObservation()
        monitor.observe(frame(0xbc, SINGLE), 100)
        snap = monitor.snapshot(100.1)
        self.assertEqual(snap['candidate_count'], 1)
        self.assertFalse(snap['lock_verified'])
        monitor.observe(frame(0x14, bytes.fromhex('0505000100'), cs=0x23), 100.2)
        self.assertEqual(monitor.snapshot(100.3)['task']['phase_raw'], 5)
        self.assertFalse(monitor.snapshot(100.3)['lock_verified'])
        self.assertIsNone(monitor.snapshot(102)['candidate_count'])
        self.assertEqual(monitor.snapshot(102)['candidates'], [])

    def test_empty_detection_clears_candidates_and_invalid_frame_does_not_authorize(self):
        monitor = SubjectObservation()
        monitor.observe(frame(0xbc, SINGLE), 100)
        monitor.observe(frame(0xbc, EMPTY), 100.2)
        self.assertEqual(monitor.snapshot(100.3)['candidate_count'], 0)
        corrupted = bytearray(frame(0xbc, SINGLE))
        corrupted[-1] ^= 1
        monitor.observe(bytes(corrupted), 100.4)
        monitor.observe(frame(0xbc, SINGLE, sender=2), 100.5)
        self.assertEqual(monitor.snapshot(100.6)['candidates'], [])
        self.assertFalse(monitor.snapshot(100.6)['lock_verified'])

    def test_unknown_relevant_format_is_reported_without_using_old_candidates(self):
        monitor = SubjectObservation()
        monitor.observe(frame(0xbc, SINGLE), 100)
        monitor.observe(frame(0xbc, b'\xff'*12), 100.1)
        snap = monitor.snapshot(100.2)
        self.assertIsNone(snap['candidate_count'])
        self.assertEqual(snap['candidates'], [])
        self.assertEqual(snap['unsupported_frames'], 1)

    def test_task_tail_is_retained_without_inventing_target_lock(self):
        value = parse_task_state(bytes.fromhex('0000000101'))
        self.assertEqual(value['tail_raw'], '0101')
        self.assertEqual(value['phase_raw'], 0)
        with self.assertRaises(ValueError):
            parse_task_state(b'\x00')
