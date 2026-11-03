"""Bounded single-axis flight directions and human command parsing."""

import math
import re
import unicodedata


DEFAULT_DURATION = 0.5
MAX_DURATION = 10.0
MOVEMENTS = {
    'forward': ('前进', 'pitch', 1359),
    'backward': ('后退', 'pitch', 689),
    'up': ('上升', 'throttle', 1359),
    'down': ('下降', 'throttle', 689),
    'left': ('左移', 'roll', 689),
    'right': ('右移', 'roll', 1359),
    'turn_left': ('左转', 'yaw', 689),
    'turn_right': ('右转', 'yaw', 1359),
}
ALIASES = {name: name for name in MOVEMENTS}
ALIASES.update({spec[0]: name for name, spec in MOVEMENTS.items()})
COMMAND_HELP = ('命令：状态 | 基础查询 | 查询设备 | 起飞 | 降落 | 回中 | 退出\n'
                '落地后重飞：确认实际落地、已收到降落回复后，输入“再次起飞”\n'
                '方向：前进 / 后退 / 上升 / 下降 / 左移 / 右移 / 左转 / 右转 [秒数]\n'
                '例：后退0.5 或 左转 0.5；不写时间默认0.5秒，最长10秒，结束自动四轴回中')


def validate_duration(seconds):
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_DURATION:
        raise ValueError(f'动作时间须大于0且不超过{MAX_DURATION:g}秒')


def parse_movement_command(text):
    text = unicodedata.normalize('NFKC', text).strip().lower()
    for alias in sorted(ALIASES, key=len, reverse=True):
        if not text.startswith(alias):
            continue
        suffix = text[len(alias):].strip()
        if not suffix:
            return ALIASES[alias], DEFAULT_DURATION
        match = re.fullmatch(r'(\d+(?:\.\d*)?|\.\d+)\s*(?:秒|s)?', suffix)
        if match is None:
            raise ValueError(f'无法识别动作时间；例如“{MOVEMENTS[ALIASES[alias]][0]} 0.5”')
        seconds = float(match.group(1))
        validate_duration(seconds)
        return ALIASES[alias], seconds
    return None
