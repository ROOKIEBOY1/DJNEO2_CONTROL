import unittest

from movement_commands import parse_movement_command


class MovementCommandTests(unittest.TestCase):
    def test_chinese_commands_with_optional_space_duration_and_unit(self):
        for text, expected in (
            ('前进1', ('forward', 1)), ('前进', ('forward', 0.5)),
            ('后退 0.5', ('backward', 0.5)), ('上升０．５秒', ('up', 0.5)),
            ('下降.5', ('down', 0.5)), ('左移', ('left', 0.5)),
            ('右移 1秒', ('right', 1)), ('左转0.5', ('turn_left', 0.5)),
            ('右转 2', ('turn_right', 2)), ('forward 1', ('forward', 1)),
            ('前进10秒', ('forward', 10)),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_movement_command(text), expected)

    def test_invalid_or_unbounded_duration_is_not_executed(self):
        for text in ('前进0', '上升-1', '后退10.01', '下降nan', '左转inf',
                     '右转0.5 junk', '左移1 2'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_movement_command(text)
        self.assertIsNone(parse_movement_command('起飞'))


