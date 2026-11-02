"""Bounded channel-3 reliable stream assembly for a requested media directory."""
import threading
from outer import parse_outer
from control_packets import valid_frames
from media_directory import parse_directory_frames


class DirectoryStream:
    def __init__(self,*,outer_session,file_session,first_sequence,max_bytes=4*1024*1024):
        self.outer_session=outer_session
        self.file_session=file_session
        self.next_sequence=first_sequence
        self.max_bytes=max_bytes
        self.done=threading.Event()
        self.entries=None
        self.error=None
        self._pending={}
        self._buffer=bytearray()
        self._frames=[]
        self._received_bytes=0

    def feed(self,raw):
        if self.done.is_set():return
        try:
            packet=parse_outer(raw)
            if packet.session!=self.outer_session or packet.channel!=3:return
            if len(raw)<=20:raise ValueError('empty directory stream packet')
            delta=(packet.sequence-self.next_sequence)&0xffff
            if delta>=0x8000:return  # Already consumed retransmission.
            if delta%8 or delta>8*127:raise ValueError('directory stream sequence outside receive window')
            data=bytes(raw[20:])
            old=self._pending.get(packet.sequence)
            if old is not None and old!=data:raise ValueError('conflicting directory stream duplicate')
            self._pending[packet.sequence]=data
            while self.next_sequence in self._pending:
                data=self._pending.pop(self.next_sequence)
                self.next_sequence=(self.next_sequence+8)&0xffff
                self._received_bytes+=len(data)
                if self._received_bytes>self.max_bytes:raise ValueError('directory exceeds size bound')
                self._buffer.extend(data)
                self._consume()
                if self.done.is_set():return
        except ValueError as exc:
            self.error=str(exc)
            self.done.set()

    def _consume(self):
        while len(self._buffer)>=4:
            if self._buffer[0]!=0x55:raise ValueError('directory stream lacks DUML header')
            size=int.from_bytes(self._buffer[1:3],'little')&0x3ff
            if size<13:raise ValueError('invalid directory DUML length')
            if len(self._buffer)<size:return
            frame=bytes(self._buffer[:size])
            del self._buffer[:size]
            if next(valid_frames(frame),None)!=frame:raise ValueError('directory DUML CRC failed')
            if frame[4:6]!=b'\xe1\x02' or frame[8:11]!=b'\x00\x00\x27':continue
            payload=frame[11:-2]
            if len(payload)<10:raise ValueError('short directory transfer header')
            if int.from_bytes(payload[4:6],'little')!=self.file_session:continue
            self._frames.append(frame)
            if int.from_bytes(payload[2:4],'little')>>12==1:
                self.entries=parse_directory_frames(self._frames,session_id=self.file_session)
                self.done.set()
                return
