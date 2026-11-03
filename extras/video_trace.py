"""Bounded Annex-B stream and decoder progress for offline freeze diagnosis."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import datetime
import json
from pathlib import Path
import secrets
import time

class VideoTrace:
    def __init__(self,directory,max_bytes=128*1024**2,max_records=20000):
        if not 0<max_bytes<=256*1024**2 or not 0<max_records<=20000:raise ValueError('invalid trace bounds')
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        zone=datetime.timezone(datetime.timedelta(hours=8))
        name='video-'+datetime.datetime.now(zone).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
        self.stream_path=directory/(name+'.hevc')
        self.index_path=directory/(name+'.jsonl')
        self._stream=self.stream_path.open('xb')
        try:self._index=self.index_path.open('x',encoding='utf-8')
        except Exception:self._stream.close();raise
        self.max_bytes=max_bytes;self.max_records=max_records
        self.bytes=0;self.records=0;self.capped=False;self.closed=False
        self.error=None
        self._last_flush=time.monotonic()

    def begin(self,payload):
        if self.closed or self.capped:return
        if self.bytes+len(payload)>self.max_bytes or self.records>=self.max_records:
            self.capped=True;self.close();return
        record=dict(at=time.time(),offset=self.bytes,length=len(payload))
        try:
            self._stream.write(payload)
            # Flush before decoder entry so triggering bytes survive a hang.
            self._stream.flush()
        except OSError as exc:
            self.error=str(exc)[:300]
            raise
        self.bytes+=len(payload);self.records+=1
        return record

    def complete(self,record,progress):
        if record is None or self.closed:return
        try:
            self._index.write(json.dumps(dict(record,**progress),ensure_ascii=False)+'\n')
            if time.monotonic()-self._last_flush>=1:
                self._index.flush();self._last_flush=time.monotonic()
        except OSError as exc:
            self.error=str(exc)[:300]
            raise

    def record(self,payload,progress):
        self.complete(self.begin(payload),progress)

    def status(self):
        return dict(stream_path=str(self.stream_path),index_path=str(self.index_path),
                    bytes=self.bytes,records=self.records,capped=self.capped,closed=self.closed,error=self.error)

    def close(self):
        if self.closed:return
        self.closed=True
        try:self._stream.close()
        finally:self._index.close()
        self.stream_path.with_suffix('.hevc.summary.json').write_text(
            json.dumps(self.status(),indent=2),encoding='utf-8')
