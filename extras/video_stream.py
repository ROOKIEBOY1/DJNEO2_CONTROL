"""Bounded, receive-only Neo video assembly, HEVC decoding and loopback preview.

No aircraft commands or network discovery. Codec imports happen in the worker;
missing/broken optional dependencies are reported in status instead of flight I/O.
"""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import queue
import threading
import time

from video_packets import VideoAssembler




class _Bits:
    def __init__(self, data):
        # HEVC emulation prevention applies only after two consecutive zeroes.
        import re
        rbsp = re.sub(b'\\x00\\x00\\x03(?=[\\x00-\\x03])', lambda _: bytes(2), data)
        self.bits = ''.join(f'{x:08b}' for x in rbsp)
        self.pos = 0

    def read(self, n=1):
        if n < 0 or self.pos + n > len(self.bits):
            raise ValueError('truncated parameter set')
        value = int(self.bits[self.pos:self.pos + n] or '0', 2)
        self.pos += n
        return value

    def ue(self):
        zeros = 0
        while self.read() == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError('oversized parameter field')
        return (1 << zeros) - 1 + self.read(zeros)


def _profile_tier_level(bits, layers):
    bits.read(96)
    flags = [(bits.read(), bits.read()) for _ in range(layers)]
    if layers:
        bits.read(2 * (8 - layers))
    for profile, level in flags:
        if profile:
            bits.read(88)
        if level:
            bits.read(8)


def _timing_signature(nal):
    """Identify only VPS/SPS timing-only repeats, conservatively.

    Preserve every RBSP bit except num_units_in_tick and time_scale. Unsupported
    syntax is forwarded unchanged; parameter ID and decoding fields must match.
    """
    if len(nal) < 2 or nal[0] & 1 or nal[1] != 1:
        raise ValueError('unsupported parameter layer')
    kind = (nal[0] >> 1) & 63
    if kind not in (32, 33):
        raise ValueError('not a timing parameter set')
    if len(nal) > 4096:
        raise ValueError('unsupported parameter size')
    bits = _Bits(nal[2:])
    if kind == 32:
        ident = bits.read(4)
        bits.read(2)
        max_layers = bits.read(6)
        layers = bits.read(3)
        bits.read(17)
        _profile_tier_level(bits, layers)
        ordering = bits.read()
        for _ in range(0 if ordering else layers, layers + 1):
            for _ in range(3): bits.ue()
        layer_id = bits.read(6)
        sets = bits.ue()
        if sets > 1024 or max_layers > 0:
            raise ValueError('unsupported multilayer VPS')
        bits.read(sets * (layer_id + 1))
    elif kind == 33:
        bits.read(4)
        layers = bits.read(3)
        bits.read()
        _profile_tier_level(bits, layers)
        ident = bits.ue()
        if ident > 15: raise ValueError('invalid SPS id')
        chroma = bits.ue()
        if chroma > 3: raise ValueError('invalid chroma format')
        if chroma == 3: bits.read()
        bits.ue(); bits.ue()  # dimensions
        if bits.read():
            for _ in range(4): bits.ue()
        bits.ue(); bits.ue()
        poc_bits = bits.ue() + 4
        if poc_bits > 16: raise ValueError('invalid POC length')
        ordering = bits.read()
        for _ in range(0 if ordering else layers, layers + 1):
            for _ in range(3): bits.ue()
        for _ in range(6): bits.ue()
        if bits.read() and bits.read():
            raise ValueError('unsupported scaling lists')
        bits.read(2)
        if bits.read():
            bits.read(8); bits.ue(); bits.ue(); bits.read()
        rps_count = bits.ue()
        if rps_count > 64: raise ValueError('oversized short term RPS')
        deltas = []
        for index in range(rps_count):
            if index and bits.read():
                bits.read(); bits.ue()
                count = 0
                for _ in range(deltas[-1] + 1):
                    used = bits.read()
                    if used or bits.read(): count += 1
            else:
                negative, positive = bits.ue(), bits.ue()
                count = negative + positive
                if count > 64: raise ValueError('oversized RPS')
                for _ in range(count): bits.ue(); bits.read()
            deltas.append(count)
        if bits.read():
            count = bits.ue()
            if count > 32: raise ValueError('oversized long term RPS')
            bits.read(count * (poc_bits + 1))
        bits.read(2)
        if not bits.read(): raise ValueError('no VUI')
        if bits.read():
            if bits.read(8) == 255: bits.read(32)
        if bits.read(): bits.read()
        if bits.read():
            bits.read(4)
            if bits.read(): bits.read(24)
        if bits.read(): bits.ue(); bits.ue()
        bits.read(3)
        if bits.read():
            for _ in range(4): bits.ue()
    else:
        raise ValueError('not a timing parameter set')
    if not bits.read(): raise ValueError('no timing info')
    offset = bits.pos
    tick, scale = bits.read(32), bits.read(32)
    if not tick or not scale: raise ValueError('invalid timing info')
    return kind, ident, bits.bits[:offset] + '0' * 64 + bits.bits[offset + 64:]


class _TimingParameterFilter:
    """Keep the active decoder parameters across timing-only retransmissions."""
    def __init__(self):
        self._signatures = {}
        self.suppressed = 0

    def feed(self, payload):
        import re
        starts = list(re.finditer(b'\\x00\\x00(?:\\x00)?\\x01', payload))
        if not starts:
            return payload
        output = [payload[:starts[0].start()]]
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(payload)
            nal = payload[match.end():end]
            try:
                signature = _timing_signature(nal)
            except (ValueError, IndexError):
                if nal and (nal[0] >> 1) & 63 in (32, 33):
                    kind = (nal[0] >> 1) & 63
                    self._signatures = {k: v for k, v in self._signatures.items() if k[0] != kind}
                output.append(payload[match.start():end])
                continue
            key = signature[:2]
            if self._signatures.get(key) == signature:
                self.suppressed += 1
                continue
            self._signatures[key] = signature
            output.append(payload[match.start():end])
        return b''.join(output)


class HEVCDecoder:
    def __init__(self):
        import av
        # PyAV disables FFmpeg log callbacks by default; warnings otherwise
        # disappear without raising exceptions or entering status().
        if av.logging.get_level() is None or av.logging.get_level() < av.logging.WARNING:
            av.logging.set_level(av.logging.WARNING)
        self._codec = av.CodecContext.create('hevc', 'r')
        self._codec.thread_count = 1
        self._parameters = _TimingParameterFilter()
        self.corrupt_frames = 0
        self.warning_count = 0
        self.last_warning = None

    def feed(self, payload):
        import av
        payload = self._parameters.feed(payload)
        # parse(b'') flushes the parser: a suppressed metadata-only payload
        # must not flush an ongoing access unit.
        if not payload:
            return []
        images = []
        with av.logging.Capture(local=True) as messages:
            try:
                for packet in self._codec.parse(payload):
                    for frame in self._codec.decode(packet):
                        if frame.is_corrupt:
                            self.corrupt_frames += 1
                            continue
                        output = BytesIO()
                        frame.to_image().save(output, format='JPEG', quality=80)
                        images.append(output.getvalue())
            finally:
                for level, _, message in messages:
                    if level <= av.logging.WARNING:
                        self.warning_count += 1
                        self.last_warning = message.strip()[:500]
        return images


_PAGE = '''<!doctype html><meta charset="utf-8"><title>无人机实时画面</title>
<style>body{background:#111;color:#eee;font:16px sans-serif;text-align:center}img{max-width:96vw;max-height:80vh}.stale{opacity:.35}</style>
<h2>无人机实时画面</h2><p id="status">等待视频</p><p id="detail"></p><img id="video">
<script>const im=document.getElementById('video');let shown=0;
const labels={closed:'预览已关闭',worker_failed:'解码线程已停止',waiting:'等待视频',
waiting_decode:'已收到视频，等待可解码画面',receiving:'实时画面',
frozen:'画面已冻结：仍在收包，但解码没有新画面',receive_stopped:'视频收包已停止，画面已过期'};
async function update(){try{const r=await fetch('status');if(!r.ok)throw Error('status');const s=await r.json();
document.getElementById('status').textContent=labels[s.state]||s.state;
im.className=s.state==='receiving'?'':'stale';
document.getElementById('detail').textContent='已解码 '+s.decoded_frames+' 帧 | 画面距今 '+
(s.last_frame_age_s===null?'无':s.last_frame_age_s.toFixed(1)+' 秒')+' | 解码警告 '+s.decoder_warnings+
(s.last_decoder_warning?' | '+s.last_decoder_warning:'')+(s.last_error?' | '+s.last_error:'');
if(s.decoded_frames>shown){shown=s.decoded_frames;im.src='frame.jpg?t='+shown;}
}catch(e){document.getElementById('status').textContent='预览连接已中断，显示的是旧画面';im.className='stale';}
setTimeout(update,250)}update();</script>'''.encode('utf-8')


class VideoReceiver:
    """submit() never waits for queue space, decoding, HTTP or flight locks."""
    def __init__(self, preview=True, queue_size=256):
        if queue_size < 1:
            raise ValueError('queue_size must be positive')
        self._queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._assembler = VideoAssembler()
        self._preview = preview
        self._server = None
        self._url = None
        self._jpeg = None
        self._last_frame = None
        self._last_receive = None
        self._last_assembly = None
        self._stats = dict(received_packets=0, queue_drops=0, decoded_frames=0,
                           assembled_frames=0, decode_errors=0, last_error=None,
                           decoder_warnings=0, last_decoder_warning=None, corrupt_frames=0,
                           timing_parameters_suppressed=0)
        self._worker = threading.Thread(target=self._run, name='video-decode', daemon=True)
        self._worker.start()

    def submit(self, raw):
        if self._stop.is_set():
            return False
        try:
            self._queue.put_nowait(bytes(raw))
            self._stats['received_packets'] += 1
            self._last_receive = time.monotonic()
            return True
        except queue.Full:
            self._stats['queue_drops'] += 1
            return False

    def _run(self):
        try:
            decoder = HEVCDecoder()
        except Exception as exc:
            self._stats['last_error'] = str(exc)
            self._stats['decode_errors'] += 1
            return
        while not self._stop.is_set():
            try:
                raw = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                payload = self._assembler.feed(raw)
                if payload is None:
                    continue
                self._stats['assembled_frames'] += 1
                self._last_assembly = time.monotonic()
                images = decoder.feed(payload)
                self._stats['decoder_warnings'] = getattr(decoder, 'warning_count', 0)
                self._stats['last_decoder_warning'] = getattr(decoder, 'last_warning', None)
                self._stats['corrupt_frames'] = getattr(decoder, 'corrupt_frames', 0)
                self._stats['timing_parameters_suppressed'] = getattr(getattr(decoder, '_parameters', None), 'suppressed', 0)
                for jpeg in images:
                    with self._lock:
                        self._jpeg = jpeg
                        self._last_frame = time.monotonic()
                        self._stats['decoded_frames'] += 1
            except Exception as exc:
                self._stats['decode_errors'] += 1
                self._stats['last_error'] = str(exc)
            finally:
                self._queue.task_done()

    def status(self):
        with self._lock:
            now = time.monotonic()
            age = None if self._last_frame is None else round(now - self._last_frame, 3)
            receive_age = None if self._last_receive is None else round(now - self._last_receive, 3)
            assembly_age = None if self._last_assembly is None else round(now - self._last_assembly, 3)
            if self._stop.is_set(): state = 'closed'
            elif not self._worker.is_alive(): state = 'worker_failed'
            elif receive_age is None: state = 'waiting'
            elif receive_age > 2: state = 'receive_stopped'
            elif age is None: state = 'waiting_decode'
            elif age > 2: state = 'frozen'
            else: state = 'receiving'
            return dict(self._stats, state=state, last_frame_age_s=age,
                        last_receive_age_s=receive_age, last_assembly_age_s=assembly_age, preview_url=self._url,
                        queued_packets=self._queue.qsize(),
                        dropped_frames=self._assembler.dropped_frames,
                        invalid_packets=self._assembler.invalid_packets,
                        duplicate_packets=self._assembler.duplicate_packets,
                        worker_alive=self._worker.is_alive(), closed=self._stop.is_set())

    def start(self):
        if not self._preview or self._stop.is_set():
            return None
        if self._server is not None:
            return self._url
        receiver = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                import json
                path = self.path.split('?', 1)[0]
                if path == '/':
                    body, mime, code = _PAGE, 'text/html; charset=utf-8', 200
                elif path == '/status':
                    body, mime, code = json.dumps(receiver.status()).encode(), 'application/json', 200
                elif path == '/frame.jpg':
                    with receiver._lock:
                        body = receiver._jpeg
                    body, mime, code = (body, 'image/jpeg', 200) if body else (b'Waiting for video', 'text/plain', 503)
                else:
                    body, mime, code = b'Not found', 'text/plain', 404
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self._url = f'http://127.0.0.1:{self._server.server_port}/'
        threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': .1},
                         name='video-preview', daemon=True).start()
        return self._url

    def close(self):
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._worker.join(timeout=2)
