import unittest
from analyze_subject_capture import analyze_subjects
from test_subject_observation import EMPTY, SINGLE, TRACKED

def record(at, sender=0x92, receiver=2, cs=10, ci=0xbc, payload=EMPTY.hex(), side='plane', session=1):
    return dict(at=at, sender=sender, receiver=receiver, command_set=cs, command_id=ci,
                payload=payload, side=side, session=session, sequence=int(at*10), flags=0,
                tunneled=True)

def osd(at, flags):
    payload = bytearray(85)
    payload[30], payload[32] = 6, flags
    return record(at, sender=3, receiver=14, cs=3, ci=0x43, payload=payload.hex())

class SubjectAnalysisTests(unittest.TestCase):
    def test_start_before_detection_and_motors_is_reported_without_prelock_claim(self):
        records = [record(99), osd(99, 0),
                   record(100, 2, 0x92, 0x23, 0x13, '010500', 'app'),
                   record(101, payload=SINGLE.hex()), osd(102, 8), osd(103, 12)]
        result = analyze_subjects(records)
        run = result['runs'][0]
        self.assertEqual(run['prestart_detection']['count'], 0)
        self.assertFalse(run['prestart_flight']['motors_on'])
        self.assertEqual(run['first_candidate']['at'], 101)
        self.assertEqual(run['first_motor_on']['at'], 102)
        self.assertEqual(run['first_airborne']['at'], 103)
        self.assertFalse(result['lock_verified'])

    def test_another_session_cannot_supply_subject_or_flight_evidence(self):
        records = [record(100, 2, 0x92, 0x23, 0x13, '010500', 'app'),
                   record(101, payload=SINGLE.hex(), session=2), dict(osd(102, 12), session=2)]
        run = analyze_subjects(records)['runs'][0]
        self.assertIsNone(run['first_candidate'])
        self.assertIsNone(run['first_motor_on'])

    def test_independent_coordinate_stream_comparison_is_session_scoped(self):
        records = [record(100, payload=SINGLE.hex()), record(100.05, ci=0x95, payload=TRACKED.hex())]
        result = analyze_subjects(records)
        self.assertEqual(result['box_crosscheck']['paired_samples'], 1)
        self.assertLess(result['box_crosscheck']['max_component_error'], .01)
        records[1]['session'] = 2
        self.assertEqual(analyze_subjects(records)['box_crosscheck']['paired_samples'], 0)
