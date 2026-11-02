"""Measured speed calibration and bounded, approximate motion plans."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
from voice_commands import ALIASES
from movement_commands import MAX_DURATION, validate_duration

DEFAULT_CALIBRATION = Path(__file__).resolve().parents[1] / '.state' / 'voice-calibration.json'

class CalibrationRequired(ValueError):
    pass

class CalibrationStore:
    def __init__(self, path=DEFAULT_CALIBRATION):
        self.path = Path(path)
        self.samples = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def record_sample(self, direction, seconds, *, distance_m=None, angle_degrees=None):
        amount = angle_degrees if direction.startswith('turn_') else distance_m
        if direction not in ALIASES or amount is None or not all(math.isfinite(x) for x in (seconds, amount)) or not 0 < seconds <= MAX_DURATION or amount <= 0:
            raise ValueError(f'请填写同方向实测位移/角度及 0–{MAX_DURATION:g} 秒的实际动作时间')
        samples = self.samples.setdefault(direction, [])
        samples.append({'seconds': seconds, 'amount': amount})
        self.samples[direction] = samples[-5:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.samples, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def rate(self, direction):
        samples = self.samples.get(direction, [])
        if not samples:
            raise CalibrationRequired('这个方向还没有实测标定；可先使用“一点”或秒数')
        value = statistics.median(s['amount'] / s['seconds'] for s in samples)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('标定数据无效')
        return value

@dataclass(frozen=True)
class MotionPlan:
    direction: str
    segments: tuple
    estimated: bool
    target: float
    unit: str

def plan_motion(intent, store):
    amount = intent.amount
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError('动作数值无效')
    if intent.unit == 'seconds':
        validate_duration(amount)
        return MotionPlan(intent.direction, (amount,), False, amount, intent.unit)
    limit = 10 if intent.unit == 'meters' else 180
    if amount > limit:
        raise ValueError(f'单次最多 {limit}，请拆分指令')
    seconds = amount / store.rate(intent.direction)
    if seconds > 20:
        raise ValueError('预计动作超过 20 秒，拒绝执行；请缩短距离')
    segments = []
    while seconds > 1e-8:
        segments.append(min(MAX_DURATION, seconds))
        seconds -= segments[-1]
    return MotionPlan(intent.direction, tuple(segments), True, amount, intent.unit)
