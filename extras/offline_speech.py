"""Windows installed offline recognizer subprocess, with a restricted grammar."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import json
import os
from pathlib import Path
import subprocess
import tempfile
from voice_commands import ALIASES, SPECIAL, DOLLY_SETTINGS

def grammar_phrases():
    phrases = set(SPECIAL)|set(DOLLY_SETTINGS)
    numbers = ['半', '零点五', '一点五', '两'] + [str(i) for i in range(1, 21)] + [
        '一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二',
        '十五', '二十', '三十', '五十', '九十', '一百']
    for direction, aliases in ALIASES.items():
        units = ('秒', '度') if direction.startswith('turn_') else ('秒', '米', '厘米')
        for alias in aliases:
            phrases.update(alias + suffix for suffix in ('', '一点', '一下'))
            for number in numbers:
                phrases.update(alias + number + unit for unit in units)
    for value in numbers + ['三点五', '3.5', '二点五', '2.5']:
        for prefix in ('设置环绕半径', '环绕半径', '设置半径', '半径设为', '半径', '环绕'):
            phrases.add(prefix + value + '米')
        phrases.add('以' + value + '米半径环绕')
    # Include explicit negatives as rejection alternatives rather than requiring
    # the recognizer to choose a positive command for every utterance.
    phrases.update('不要' + phrase for phrase in list(phrases))
    return sorted(phrases)

class OfflineSpeech:
    def __init__(self, check=False, emulate=None):
        state = Path(__file__).resolve().parents[1] / '.state'
        state.mkdir(exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='speech-', suffix='.json', dir=state)
        os.close(descriptor)
        self.grammar = Path(name)
        self.grammar.write_text(json.dumps(grammar_phrases(), ensure_ascii=False), encoding='utf-8')
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(Path(__file__).with_name('windows_speech.ps1')),
                   '-GrammarPath', str(self.grammar)]
        if check:
            command.append('-Check')
        if emulate:
            command.extend(['-Emulate', emulate])
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        encoding='utf-8-sig', errors='replace',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def events(self):
        for line in self.process.stdout:
            try:
                yield json.loads(line)
            except ValueError:
                yield {'type': 'error', 'message': line.strip()}
        code = self.process.wait()
        if code:
            yield {'type': 'error', 'message': self.process.stderr.read() or f'Speech process exit {code}'}

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()
        self.grammar.unlink(missing_ok=True)
