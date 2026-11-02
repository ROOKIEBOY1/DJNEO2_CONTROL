import unittest
from analyze_circle_capture import analyze

def record(at, side='plane', sender=0x92, receiver=2, cs=0x23, ci=0x14, sequence=1, flags=0, payload='0505000100'):
    return dict(at=at, side=side, session=1, sender=sender, receiver=receiver,
                command_set=cs, command_id=ci, sequence=sequence, flags=flags,
                payload=payload, tunneled=True)

class CircleAnalysisTests(unittest.TestCase):
    def test_manual_stop_needs_matching_ack_inactive_state_and_operator_confirmation(self):
        start = record(100, 'app', 2, 0x92, 0x23, 0x13, 20, 64, '010500')
        stop = record(108, 'app', 2, 0x92, 0x23, 0x15, 25, 64, '')
        ack = record(108.02, ci=0x15, sequence=25, flags=192, payload='00')
        inactive = record(108.07, sequence=26, payload='0000000101')
        records = [start, record(101, sequence=22), stop, ack, inactive,
                   record(114, sequence=30, payload='0400000100')]
        result = analyze(records, operator_confirmed_stop=True)
        self.assertTrue(result['manual_stop']['confirmed'])
        run = result['circle_start_requests'][0]
        self.assertEqual(run['termination_candidate'], 'manual_stop')
        self.assertAlmostEqual(run['active_candidate_seconds'], 7.07)
        self.assertFalse(analyze(records)['manual_stop']['confirmed'])
        self.assertEqual(analyze(records)['circle_start_requests'][0]['termination_candidate'], 'stop_requested_unconfirmed')
        for missing in (ack, inactive):
            unconfirmed = analyze([r for r in records if r is not missing], operator_confirmed_stop=True)
            self.assertFalse(unconfirmed['manual_stop']['confirmed'])
            self.assertEqual(unconfirmed['circle_start_requests'][0]['termination_candidate'], 'stop_requested_unconfirmed')

    def test_stop_is_not_attributed_to_an_already_finished_or_replaced_circle(self):
        base = [record(100, 'app', 2, 0x92, 0x23, 0x13, 20, 64, '010500'),
                record(101, sequence=22),
                record(108, 'app', 2, 0x92, 0x23, 0x15, 25, 64, ''),
                record(108.02, ci=0x15, sequence=25, flags=192, payload='00'),
                record(108.07, sequence=26, payload='0000000101')]
        for intervening in (record(105, payload='0400000100'),
                            record(105, 'app', 2, 0x92, 0x23, 0x13, 24, 64, '010600')):
            with self.subTest(intervening=intervening):
                result = analyze(base+[intervening], operator_confirmed_stop=True)
                self.assertFalse(result['manual_stop']['confirmed'])
                self.assertIsNone(result['manual_stop']['requests'][0]['circle_start_sequence'])

    def test_stop_cannot_use_another_sessions_inactive_state(self):
        records = [record(100, 'app', 2, 0x92, 0x23, 0x13, 20, 64, '010500'),
                   record(108, 'app', 2, 0x92, 0x23, 0x15, 25, 64, ''),
                   record(108.02, ci=0x15, sequence=25, flags=192, payload='00'),
                   dict(record(108.07, payload='0000000101'), session=2)]
        self.assertFalse(analyze(records, operator_confirmed_stop=True)['manual_stop']['confirmed'])

    def test_run_cannot_use_another_sessions_telemetry(self):
        start = record(100, 'app', 2, 0x92, 0x23, 0x13, 20, 64, '010500')
        records = [start, dict(record(100.1), session=2),
                   dict(record(113, payload='0400000100'), session=2)]
        run = analyze(records)['circle_start_requests'][0]
        self.assertIsNone(run['active_candidate_at'])
        self.assertIsNone(run['ending_candidate_at'])
        self.assertEqual(run['stick_axes_counts'], [])

    def test_report_timestamps_explicitly_use_hong_kong_timezone(self):
        start = record(100, 'app', 2, 0x92, 0x23, 0x13, 20, 64, '010500')
        self.assertTrue(analyze([start])['circle_start_requests'][0]['local_time'].endswith('+08:00'))

    def test_two_starts_and_completion_telemetry_do_not_establish_stop(self):
        records = []
        for at, seq in ((100, 20), (140, 21)):
            records += [record(at, 'app', 2, 0x92, 0x23, 0x13, seq, 64, '010500'),
                        record(at+.05, ci=0x13, sequence=seq, flags=192, payload='00'),
                        record(at+.1, sequence=seq+10),
                        record(at+13, sequence=seq+11, payload='0400000100')]
        records += [dict(r, at=r['at']+.000001) for r in list(records)]
        result = analyze(records)
        self.assertEqual(len(result['circle_start_requests']), 2)
        self.assertTrue(all(r['reply']['payload'] == '00' for r in result['circle_start_requests']))
        self.assertEqual(result['other_task_control_requests'], [])
        self.assertFalse(result['manual_stop']['confirmed'])

    def test_radius_write_is_matched_to_ack_and_reads_have_independent_values(self):
        records = [record(90, cs=3, ci=0xf8, payload='004693270300002041'),
                   record(100, 'app', 2, 0x92, 3, 0xf9, 21, 64, '4693270300000040'),
                   record(100.05, cs=3, ci=0xf9, sequence=21, flags=192, payload='004693270300000040'),
                   record(101, cs=3, ci=0xf8, payload='004693270300000040')]
        result = analyze(records)
        self.assertEqual([r['value_m'] for r in result['radius_readback_summary']], [2., 10.])
        self.assertEqual(result['radius_writes'][0]['reply']['payload'], '004693270300000040')
