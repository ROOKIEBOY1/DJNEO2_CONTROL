import unittest
from duml import encode_frame
from landing_status import LandingStatus

def osd(mode=6, flags=0x1c, latest=2):
    payload = bytearray(85)
    payload[16:18] = (20).to_bytes(2, 'little')
    payload[30], payload[31], payload[32] = mode, latest, flags
    return encode_frame(3, 0x0e, 1, 0, 3, 0x43, bytes(payload))

class LandingStatusTests(unittest.TestCase):
    def test_ack_does_not_mean_descending_or_landed(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(), 100.1)
        self.assertEqual(status.snapshot(100.2)['phase'], 'requested')

    def test_brief_landing_then_stable_hover_reports_interrupted(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(12), 100.2)
        self.assertEqual(status.snapshot(100.2)['phase'], 'descending')
        status.observe(osd(6), 100.5)
        self.assertEqual(status.snapshot(100.5)['phase'], 'descending')
        status.observe(osd(6), 101.1)
        self.assertEqual(status.snapshot(101.1)['phase'], 'interrupted')
        self.assertTrue(status.snapshot(101.1)['motors_on'])

    def test_motors_off_is_observed_separately_and_never_assumed_from_timeout(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(12), 100.2)
        status.observe(osd(6, flags=0), 103)
        self.assertEqual(status.snapshot(103)['phase'], 'motors_off')
        self.assertFalse(status.snapshot(103)['airborne_flag'])
        self.assertEqual(status.snapshot(110)['telemetry_age_s'], 7)

    def test_retry_starts_new_observation_and_missing_data_is_explicit(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(6), 100.2)
        self.assertEqual(status.snapshot(105)['phase'], 'telemetry_stale')
        status.observe(osd(6), 105)
        self.assertEqual(status.snapshot(105)['phase'], 'not_observed')
        status.begin(106)
        self.assertEqual(status.snapshot(106)['phase'], 'requested')

    def test_wrong_source_command_and_invalid_crc_do_not_change_status(self):
        status = LandingStatus()
        status.begin(100)
        frame = bytearray(osd(12))
        frame[-1] ^= 1
        for bad in (bytes(frame), encode_frame(2, 3, 1, 0, 3, 0x43, bytes(85)),
                    encode_frame(3, 2, 1, 0, 3, 0x2a, b'\x00')):
            status.observe(bad, 100.1)
        self.assertEqual(status.snapshot(100.2)['phase'], 'requested')
        self.assertIsNone(status.snapshot(100.2)['telemetry_age_s'])

    def test_retakeoff_does_not_inherit_previous_landing_confirmation(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(6, flags=0), 103)
        self.assertEqual(status.snapshot(103)['phase'], 'motors_off')
        status.new_flight()
        status.observe(osd(6, flags=0x1c, latest=1), 104)
        self.assertEqual(status.snapshot(104)['phase'], 'idle')
        self.assertIsNone(status.snapshot(104)['requested_at'])

    def test_new_motor_on_data_cannot_retain_motors_off_phase(self):
        status = LandingStatus()
        status.begin(100)
        status.observe(osd(6, flags=0), 101)
        self.assertEqual(status.snapshot(101)['phase'], 'motors_off')
        status.observe(osd(6, flags=0x1c), 102)
        self.assertNotEqual(status.snapshot(102)['phase'], 'motors_off')
        self.assertTrue(status.snapshot(102)['motors_on'])
