"""Closed vocabulary Chinese flight intent parser; no network access."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from dataclasses import dataclass
import re
import unicodedata
from circle_protocol import validate_radius

ALIASES = {
    'forward': ('向前飞', '往前飞', '前进', '向前', '往前'),
    'backward': ('向后飞', '往后飞', '后退', '向后', '往后'),
    'up': ('向上飞', '往上飞', '上升', '升高'),
    'down': ('向下飞', '往下飞', '下降', '降低'),
    'left': ('向左平移', '向左飞', '往左飞', '左移'),
    'right': ('向右平移', '向右飞', '往右飞', '右移'),
    'turn_left': ('向左转', '往左转', '左转'),
    'turn_right': ('向右转', '往右转', '右转'),
}
SPECIAL = {'停止': 'center', '停下': 'center', '停一下': 'center', '回中': 'center',
           '悬停': 'center', '别动': 'center', '不要动': 'center', '取消动作': 'center',
           '降落': 'land', '起飞': 'takeoff', '确认': 'confirm', '确认起飞': 'confirm_takeoff',
           '再次起飞': 'takeoff_again', '确认已落地再次起飞': 'confirm_again',
           '启用语音': 'enable', '暂停语音': 'pause', '状态': 'status', '退出': 'exit',
           '环绕': 'circle', '准备环绕': 'circle_prepare',
           '停止环绕': 'circle_stop', '结束环绕': 'circle_stop', '取消环绕': 'circle_stop',
           '拍照':'camera_photo', '照相':'camera_photo', '开始录像':'camera_record_start',
           '录像':'camera_record_start', '停止录像':'camera_record_stop', '结束录像':'camera_record_stop'}
SPECIAL.update({'相册':'camera_list','媒体列表':'camera_list',
                '下载最新照片':'camera_download_photo','下载照片':'camera_download_photo',
                '下载最新录像':'camera_download_video','下载最新视频':'camera_download_video',
                '下载录像':'camera_download_video','下载视频':'camera_download_video'})
SPECIAL.update({'滑动变焦':'dolly','开始滑动变焦':'dolly','准备滑动变焦':'dolly_prepare',
                '停止滑动变焦':'dolly_stop','结束滑动变焦':'dolly_stop','取消滑动变焦':'dolly_stop'})
DOLLY_SETTINGS={}
for framing in ('半身','全身','远景'):
    for prefix in ('','景别','拍摄景别','设置景别'):
        DOLLY_SETTINGS[prefix+framing]=('dolly_framing',framing)
for value,names in (('2',('2','二','两')),('3',('3','三'))):
    for prefix in ('倍率','变焦倍率','变焦'):
        for name in names:
            for suffix in ('','倍'):
                DOLLY_SETTINGS[prefix+name+suffix]=('dolly_zoom',value)
for value,names in (('on',('开','开启')),('off',('关','关闭'))):
    for prefix in ('渐远','变焦前渐远'):
        for name in names:
            DOLLY_SETTINGS[prefix+name]=('dolly_retreat',value)

@dataclass(frozen=True)
class VoiceIntent:
    kind: str
    direction: str = ''
    amount: float = 0
    unit: str = ''
    value: str = ''

def number(text):
    if text == '半':
        return .5
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return float(text)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    if '点' in text:
        whole, fraction = text.split('点', 1)
        if not fraction or any(c not in digits for c in fraction):
            raise ValueError('数字无法识别')
        return number(whole) + float('0.' + ''.join(str(digits[c]) for c in fraction))
    total = current = 0
    if not text:
        raise ValueError('缺少数字')
    for char in text:
        if char in digits:
            current = digits[char]
        elif char in '十百':
            total += (current or 1) * (10 if char == '十' else 100)
            current = 0
        else:
            raise ValueError('数字无法识别')
    return float(total + current)

def parse_voice_command(text):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).lower().strip('。，！!？?')
    for prefix in ('请', '无人机', '帮我'):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text in SPECIAL:
        return VoiceIntent(SPECIAL[text])
    if text in DOLLY_SETTINGS:
        kind,value=DOLLY_SETTINGS[text]
        return VoiceIntent(kind,value=value)
    numeric = r'([\d.零一二两三四五六七八九十点半]+)'
    for pattern, kind in ((r'(?:设置环绕半径|环绕半径|设置半径|半径设为|半径)' + numeric + '(?:米|m)', 'circle_radius'),
                          ('环绕' + numeric + '(?:米|m)', 'circle'),
                          ('以' + numeric + '(?:米|m)半径环绕', 'circle')):
        match = re.fullmatch(pattern, text)
        if match:
            return VoiceIntent(kind, amount=validate_radius(number(match[1])), unit='meters')
    for direction, aliases in ALIASES.items():
        for alias in aliases:
            if not text.startswith(alias):
                continue
            suffix = text[len(alias):]
            if suffix in ('', '一点', '一下'):
                return VoiceIntent('move', direction, .5, 'seconds')
            match = re.fullmatch(r'([\d.零一二两三四五六七八九十百点半]+)(厘米|cm|米|m|秒|s|度|°)?', suffix)
            if not match:
                raise ValueError('请一次只说一个明确动作')
            amount = number(match[1])
            unit = match[2] or '秒'
            if unit in ('厘米', 'cm'):
                amount /= 100
            unit = ('meters' if unit in ('厘米', 'cm', '米', 'm') else
                    'degrees' if unit in ('度', '°') else 'seconds')
            if (unit == 'meters' and direction.startswith('turn_') or
                    unit == 'degrees' and not direction.startswith('turn_') or amount <= 0):
                raise ValueError('动作的数值或单位不适用')
            return VoiceIntent('move', direction, amount, unit)
    raise ValueError('未识别口令，请使用单个方向、起飞、降落或停止')
