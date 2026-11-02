import json
from pathlib import Path
import unittest
from circle_protocol import (build_radius_frame, build_circle_start_frame, build_circle_stop_frame,
                             build_circle_selection_frame, parse_radius_value,
                             deduplicate_records, matching_reply)

FIXTURE = Path(__file__).resolve().parents[1] / 'profiles' / 'neo2-circle-evidence.json'

class CircleProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(FIXTURE.read_text(encoding='utf-8'))['evidence']

    def test_radius_write_matches_original_capture_frame(self):
        self.assertEqual(build_radius_frame(2, 52134).hex(), self.evidence['radius_write_2m']['frame_hex'])

    def test_both_starts_match_independent_captured_frames(self):
        for sequence, label in ((36190, 'start_first'), (38935, 'start_second')):
            self.assertEqual(build_circle_start_frame(sequence).hex(), self.evidence[label]['frame_hex'])

    def test_stop_frame_matches_original_capture_and_has_empty_payload(self):
        frame = build_circle_stop_frame(39638)
        self.assertEqual(frame.hex(), self.evidence['stop_request']['frame_hex'])
        self.assertEqual(len(frame), 13)
        self.assertEqual(frame[4:6], b'\x02\x92')
        self.assertEqual(frame[8:11], b'\x40\x23\x15')
        self.assertEqual(frame[11:-2], b'')

    def test_mode_selection_is_distinct_from_radius_setting(self):
        self.assertEqual(build_circle_selection_frame(51362).hex(), self.evidence['circle_selection']['frame_hex'])
        self.assertEqual(build_circle_start_frame(1)[5], 0x92)
        self.assertEqual(build_circle_selection_frame(1)[5], 0x17)

    def test_radius_readback_is_prefix_checked_and_float32(self):
        self.assertEqual(parse_radius_value(bytes.fromhex('004693270300002041')), 10)
        self.assertEqual(parse_radius_value(bytes.fromhex('004693270300000040aabb')), 2)
        for payload in ('014693270300000040', '00469327030000', '000000000000000040', '00469327030000c07f'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_radius_value(bytes.fromhex(payload))

    def test_radius_encoder_refuses_values_without_capture_evidence(self):
        for radius in (0, -1, 1.99, 10.01, float('nan'), float('inf')):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                build_radius_frame(radius, 1)

    def test_custom_radius_uses_same_float_field(self):
        self.assertEqual(build_radius_frame(5, 1)[11:-2].hex(), '469327030000a040')

    def test_deduplication_keeps_later_retries_and_other_sessions(self):
        record = dict(at=100., side='app', session=1, sender=2, receiver=146,
                      command_set=35, command_id=19, sequence=42, flags=64, payload='010500', tunneled=True)
        unique = deduplicate_records([record, dict(record), dict(record, at=100.000001),
                                      dict(record, at=101), dict(record, session=2)])
        self.assertEqual(len(unique), 3)

    def test_ack_matching_checks_endpoint_sequence_session_time_and_flags(self):
        request = dict(at=100., session=1, sender=2, receiver=146, command_set=35,
                       command_id=19, sequence=42, flags=64)
        reply = dict(request, at=100.1, sender=146, receiver=2, flags=192, payload='00')
        self.assertEqual(matching_reply(request, [reply]), reply)
        for bad in (dict(reply, session=2), dict(reply, sequence=43), dict(reply, sender=3),
                    dict(reply, at=99), dict(reply, at=104), dict(reply, flags=0)):
            self.assertIsNone(matching_reply(request, [bad]))
