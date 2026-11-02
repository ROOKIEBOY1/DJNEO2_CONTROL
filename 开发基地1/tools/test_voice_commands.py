import tempfile
from pathlib import Path
import unittest

from voice_commands import parse_voice_command
from voice_motion import CalibrationRequired, CalibrationStore, plan_motion


class VoiceCommandTests(unittest.TestCase):
    def test_natural_chinese_directions_numbers_and_units(self):
        cases = {
            '向前飞10m': ('forward', 10, 'meters'),
            '向后飞十米': ('backward', 10, 'meters'),
            '向上飞一点': ('up', 0.5, 'seconds'),
            '向下飞五十厘米': ('down', 0.5, 'meters'),
            '左移两秒': ('left', 2, 'seconds'),
            '请无人机往右飞半米': ('right', 0.5, 'meters'),
            '向左转九十度': ('turn_left', 90, 'degrees'),
            '向右转一点': ('turn_right', 0.5, 'seconds'),
            '前进０．５': ('forward', 0.5, 'seconds'),
            '向前飞一点五米': ('forward', 1.5, 'meters'),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                intent = parse_voice_command(text)
                self.assertEqual(intent.kind, 'move')
                self.assertEqual((intent.direction, intent.amount, intent.unit), expected)

    def test_negation_mixed_commands_and_wrong_units_are_rejected(self):
        for text in ('不要起飞', '向前飞十米然后上升', '向左转十米', '上升九十度',
                     '向前飞-1米', '随便飞飞'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_voice_command(text)
        self.assertEqual(parse_voice_command('别动').kind, 'center')
        self.assertEqual(parse_voice_command('确认起飞').kind, 'confirm_takeoff')
        self.assertEqual(parse_voice_command('确认').kind, 'confirm')

    def test_distance_requires_measured_calibration_and_is_segmented(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationStore(Path(directory) / 'calibration.json')
            intent = parse_voice_command('向前飞十米')
            with self.assertRaises(CalibrationRequired):
                plan_motion(intent, store)
            store.record_sample('forward', 1, distance_m=1.25)
            plan = plan_motion(intent, store)
            self.assertTrue(plan.estimated)
            self.assertAlmostEqual(sum(plan.segments), 8)
            self.assertTrue(all(0 < seconds <= 10 for seconds in plan.segments))
            self.assertEqual(plan.segments, (8.,))
            self.assertEqual(plan.target, 10)
            with self.assertRaises(ValueError):
                plan_motion(parse_voice_command('向前飞一百米'), store)

    def test_small_motion_needs_no_calibration_and_unreasonable_duration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationStore(Path(directory) / 'calibration.json')
            self.assertEqual(plan_motion(parse_voice_command('向上飞一点'), store).segments, (0.5,))
            self.assertEqual(plan_motion(parse_voice_command('向上飞十秒'), store).segments, (10.,))
            with self.assertRaises(ValueError):
                plan_motion(parse_voice_command('向上飞十一秒'), store)
            store.record_sample('up', 1, distance_m=0.1)
            with self.assertRaises(ValueError):
                plan_motion(parse_voice_command('向上飞十米'), store)

    def test_ten_second_calibration_and_distance_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationStore(Path(directory) / 'calibration.json')
            store.record_sample('forward', 10, distance_m=5)
            plan = plan_motion(parse_voice_command('向前飞十米'), store)
            self.assertEqual(plan.segments, (10., 10.))


if __name__ == '__main__':
    unittest.main()
