"""Bounded asynchronous record of received video datagrams and queue decisions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import json
from pathlib import Path
import queue
import struct
import threading
import time

HEADER=struct.Struct('<dIB')


def packet_records(path):
    with Path(path).open('rb') as source:
        while header:=source.read(HEADER.size):
            if len(header)!=HEADER.size:raise ValueError('Truncated raw-video header')
            at,size,accepted=HEADER.unpack(header)
            if not 0<size<=65535 or accepted not in (0,1):raise ValueError('Invalid raw-video record')
            raw=source.read(size)
            if len(raw)!=size:raise ValueError('Truncated raw-video packet')
            yield at,bool(accepted),raw


class VideoPacketTrace:
    def __init__(self,path,max_bytes=128*1024**2,queue_size=4096):
        if not HEADER.size<=max_bytes<=256*1024**2 or not 1<=queue_size<=16384:
            raise ValueError('Invalid packet trace bounds')
        self.path=Path(path)
        self.summary_path=self.path.with_suffix('.summary.json')
        self._file=self.path.open('xb')
        self._queue=queue.Queue(maxsize=queue_size)
        self._stop=threading.Event()
        self.max_bytes=max_bytes
        self.bytes=self.records=self.queue_drops=0
        self.capped=self.closed=False
        self.error=None
        self._worker=threading.Thread(target=self._run,name='video-packet-trace',daemon=True)
        self._worker.start()

    def submit(self,raw,accepted,at=None):
        if self._stop.is_set() or self.capped or self.error:
            return False
        raw=bytes(raw)
        if not 0<len(raw)<=65535:
            self.error='Invalid diagnostic datagram size'
            return False
        try:
            self._queue.put_nowait((time.time() if at is None else at,bool(accepted),raw))
            return True
        except queue.Full:
            self.queue_drops+=1
            return False

    def _run(self):
        last_flush=time.monotonic()
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:at,accepted,raw=self._queue.get(timeout=.05)
                except queue.Empty:continue
                try:
                    if self.bytes+HEADER.size+len(raw)>self.max_bytes:
                        self.capped=True
                        continue
                    if self.capped:continue
                    self._file.write(HEADER.pack(at,len(raw),int(accepted)))
                    self._file.write(raw)
                    self.bytes+=HEADER.size+len(raw);self.records+=1
                    if time.monotonic()-last_flush>=1:
                        self._file.flush();last_flush=time.monotonic()
                finally:self._queue.task_done()
        except (OSError,ValueError) as exc:
            self.error=str(exc)[:300]
        finally:
            try:self._file.close()
            except OSError as exc:self.error=str(exc)[:300]
            self.closed=True
            try:self.summary_path.write_text(json.dumps(self.status(),indent=2),encoding='utf-8')
            except OSError as exc:self.error=str(exc)[:300]

    def status(self):
        return dict(path=str(self.path),summary_path=str(self.summary_path),records=self.records,
                    bytes=self.bytes,queue_drops=self.queue_drops,capped=self.capped,
                    closed=self.closed,error=self.error,queued=self._queue.qsize())

    def close(self):
        self._stop.set()
        self._worker.join(2)
