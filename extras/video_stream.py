"""Bounded, receive-only Neo video assembly, HEVC decoding and loopback preview.

No aircraft commands or network discovery. Codec imports happen in the worker;
missing/broken optional dependencies are reported in status instead of flight I/O.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
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


class _LowDelayPictureOrder:
    """Repair bounded I/P transport reordering; never conceal missing pictures.

    Enable only for parsed single-layer SPS with max_num_reorder_pics=0, a
    simple PPS and first-slice I/P pictures. Other syntax passes through.
    """
    def __init__(self, max_pending=3, max_age=.12):
        self.max_pending, self.max_age = max_pending, max_age
        self._sps, self._pps = {}, {}
        self._parameter_bytes = {}
        self._active = self._expected = None
        self._pending = OrderedDict()
        self._retired = OrderedDict()
        self._serial = 0
        self._bypass = False
        self.reordered = 0

    def _reset(self):
        saved = b''.join(item[1] for item in self._pending.values())
        self._pending.clear()
        self._expected = None
        self._retired.clear()
        self._serial = 0
        return saved

    def _advance(self, poc, modulus):
        # Remember only known passed/skipped positions, within half a POC
        # cycle. An arbitrary far-forward gap cannot be called a stale frame.
        delta = (poc-self._expected) % modulus
        for step in range(max(0, delta-31), delta+1):
            self._retired[(self._expected+step) % modulus] = self._serial+step
        self._serial += delta+1
        self._expected = (poc+1) % modulus
        for key, serial in list(self._retired.items()):
            if self._serial-serial >= modulus//2:
                del self._retired[key]
        while len(self._retired) > 32:
            self._retired.popitem(last=False)

    def feed(self, payload):
        import re
        if not payload:
            return b''
        starts = list(re.finditer(b'\x00\x00(?:\x00)?\x01', payload))
        if not starts:
            return self._reset() + payload
        pictures = []
        try:
            for i, match in enumerate(starts):
                end = starts[i+1].start() if i+1 < len(starts) else len(payload)
                nal = payload[match.end():end]
                if len(nal) < 2 or nal[0] & 1 or nal[1] != 1:
                    raise ValueError('unsupported layer')
                kind = nal[0] >> 1 & 63
                bits = _Bits(nal[2:130])
                if kind == 33:
                    bits.read(4); layers = bits.read(3); bits.read()
                    if layers: raise ValueError('temporal layers')
                    _profile_tier_level(bits, layers)
                    ident = bits.ue(); chroma = bits.ue()
                    if ident > 15: raise ValueError('SPS id')
                    if chroma not in (0, 1, 2): raise ValueError('colour planes')
                    bits.ue(); bits.ue()
                    if bits.read():
                        for _ in range(4): bits.ue()
                    bits.ue(); bits.ue(); width = bits.ue() + 4
                    if not 4 <= width <= 16: raise ValueError('POC width')
                    bits.read(); bits.ue(); reorder = bits.ue(); bits.ue()
                    self._sps[ident] = width if reorder == 0 else None
                elif kind == 34:
                    ident, sps = bits.ue(), bits.ue()
                    if ident > 63 or sps > 15: raise ValueError('PPS id')
                    dependent, output, extra = bits.read(), bits.read(), bits.read(3)
                    self._pps[ident] = (sps, output, extra) if not dependent else None
                elif kind <= 31:
                    if kind not in (1, 19, 20): raise ValueError('unsupported picture')
                    if not bits.read(): raise ValueError('not first slice')
                    if kind in (19, 20): bits.read()
                    pps = self._pps.get(bits.ue())
                    if pps is None: raise ValueError('unsupported PPS')
                    sps, output, extra = pps
                    width = self._sps.get(sps)
                    if width is None: raise ValueError('reordering SPS')
                    bits.read(extra)
                    if bits.ue() not in (1, 2): raise ValueError('B picture')
                    if output: bits.read()
                    poc = 0 if kind in (19, 20) else bits.read(width)
                    pictures.append((kind, poc, 1 << width, (sps, pps, width)))
                if kind in (33, 34):
                    key = (kind, ident)
                    previous = self._parameter_bytes.get(key)
                    if previous is not None and previous != nal:
                        self._pending.clear(); self._expected = self._active = None
                    self._parameter_bytes[key] = nal
        except (ValueError, IndexError):
            self._sps.clear(); self._pps.clear(); self._active = None
            return self._reset() + payload
        if not pictures:
            return payload
        if len(pictures) != 1:
            return self._reset() + payload
        kind, poc, modulus, active = pictures[0]
        if active != self._active or kind in (19, 20):
            self._reset(); self._active = active; self._bypass = False
        if self._bypass:
            return payload
        if self._expected is None:
            self._expected = poc
            self._advance(poc, modulus)
            return payload
        now = time.monotonic()
        output = []
        # Resolve the existing bounded gap before rejecting an ambiguous POC.
        # Otherwise a far-forward picture at the half-range boundary could be
        # dropped repeatedly until a full wrap aliases old decoder references.
        if (poc - self._expected) % modulus >= modulus // 2 and self._pending:
            oldest = next(iter(self._pending.values()))[0]
            if len(self._pending) + 1 >= self.max_pending or now - oldest >= self.max_age:
                while self._pending:
                    nearest = min(self._pending, key=lambda p: (p-self._expected) % modulus)
                    _, data = self._pending.pop(nearest)
                    output.append(data)
                    self._advance(nearest, modulus)
        # Once a gap has been released, its late predecessors must not rewind
        # the reference chain. Serial-number half-range arithmetic also keeps
        # the normal maximum-POC -> zero transition in the forward direction.
        if (poc - self._expected) % modulus >= modulus // 2:
            if poc in self._retired:
                return b''.join(output)
            # No known predecessor proves this is old: a real large loss is
            # indistinguishable from a late POC across wrap. Stop sorting until
            # a refresh/new parameter sequence; let the codec reject damage.
            self._bypass = True
            return b''.join(output) + self._reset() + payload
        self._pending[poc] = (now, payload)
        while self._pending:
            if self._expected not in self._pending:
                oldest = next(iter(self._pending.values()))[0]
                if len(self._pending) < self.max_pending and now - oldest < self.max_age:
                    break
                # Genuine loss still reaches FFmpeg's normal corruption checks.
                selected = min(self._pending, key=lambda p: (p-self._expected) % modulus)
            else:
                selected = self._expected
            if next(iter(self._pending)) != selected: self.reordered += 1
            _, data = self._pending.pop(selected)
            output.append(data)
            self._advance(selected, modulus)
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
        self._picture_order = _LowDelayPictureOrder()
        self.corrupt_frames = 0
        self.warning_count = 0
        self.last_warning = None

    def feed(self, payload):
        import av
        payload = self._picture_order.feed(self._parameters.feed(payload))
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
<style>body{background:#111;color:#eee;font:16px sans-serif;text-align:center}img{display:block;max-width:96vw;max-height:76vh}.stale{opacity:.35}
#stage{position:relative;display:inline-block;line-height:0}svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}</style>
<h2>无人机实时画面</h2><p id="status">等待视频</p><p id="detail"></p>
<div id="stage"><img id="video"><svg id="boxes" viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg></div>
<p>绿框：飞机回传的跟踪框；黄框：候选主体。框不单独代表已锁定或正在跟随。</p>
<script>const im=document.getElementById('video'),overlay=document.getElementById('boxes');let shown=0,lastImageLoad=0,boxExpires=0,latestBoxes=[],videoOkay=false;
function paintBoxes(){const now=performance.now();drawBoxes(videoOkay&&now<=boxExpires&&lastImageLoad>0&&im.complete&&im.naturalWidth>0&&now-lastImageLoad<=1000?latestBoxes:[]);}
im.onload=()=>{lastImageLoad=performance.now();paintBoxes();};im.onerror=()=>{lastImageLoad=0;overlay.replaceChildren();};
setInterval(()=>{if(performance.now()>boxExpires||performance.now()-lastImageLoad>1000)overlay.replaceChildren();},100);
async function fetchJson(path){const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),800);
try{const r=await fetch(path,{signal:abort.signal});if(!r.ok)throw Error(path);return await r.json();}finally{clearTimeout(timer);}}
function drawBoxes(boxes){overlay.replaceChildren();for(const b of boxes){
const color=b.source==='tracking'?'#28ed70':'#ffd44a',ns='http://www.w3.org/2000/svg';
const r=document.createElementNS(ns,'rect');for(const [k,v] of Object.entries({x:b.left*1000,y:b.top*1000,width:b.width*1000,height:b.height*1000,fill:'none',stroke:color,'stroke-width':3}))r.setAttribute(k,v);overlay.appendChild(r);
const t=document.createElementNS(ns,'text');t.setAttribute('x',b.left*1000+5);t.setAttribute('y',Math.max(28,b.top*1000+28));t.setAttribute('fill',color);t.setAttribute('font-size',25);t.textContent=(b.source==='tracking'?'跟踪框 #':'候选 #')+b.id;overlay.appendChild(t);}}
const labels={closed:'预览已关闭',worker_failed:'解码线程已停止',waiting:'等待视频',
waiting_decode:'已收到视频，等待可解码画面',receiving:'实时画面',
frozen:'画面已冻结：仍在收包，但解码没有新画面',receive_stopped:'视频收包已停止，画面已过期'};
async function update(){try{const s=await fetchJson('status'),sampled=performance.now();
document.getElementById('status').textContent=labels[s.state]||s.state;
im.className=s.state==='receiving'?'':'stale';
document.getElementById('detail').textContent='已解码 '+s.decoded_frames+' 帧 | 画面距今 '+
(s.last_frame_age_s===null?'无':s.last_frame_age_s.toFixed(1)+' 秒')+' | 解码警告 '+s.decoder_warnings+
(s.last_decoder_warning?' | '+s.last_decoder_warning:'')+(s.last_error?' | '+s.last_error:'');
if(s.decoded_frames>shown){shown=s.decoded_frames;im.src='frame.jpg?t='+shown;}
try{const targets=await fetchJson('subjects'),now=performance.now();boxExpires=now+400;
latestBoxes=targets.boxes;videoOkay=s.state==='receiving'&&s.last_frame_age_s+(now-sampled)/1000<=1;paintBoxes();}catch(e){latestBoxes=[];drawBoxes([]);}
}catch(e){document.getElementById('status').textContent='预览连接已中断，显示的是旧画面';im.className='stale';videoOkay=false;drawBoxes([]);}
setTimeout(update,250)}update();</script>'''.encode('utf-8')


class VideoReceiver:
    """submit() never waits for queue space, decoding, HTTP or flight locks."""
    def __init__(self, preview=True, queue_size=256,diagnostics_dir=None):
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
        self._subject_provider = None
        self._stats = dict(received_packets=0, queue_drops=0, decoded_frames=0,
                           assembled_frames=0, decode_errors=0, last_error=None,
                           decoder_warnings=0, last_decoder_warning=None, corrupt_frames=0,
                           timing_parameters_suppressed=0)
        self._trace=None
        self._packet_trace=None
        self._packet_trace_failed=False
        if diagnostics_dir is not None:
            try:
                from video_trace import VideoTrace
                self._trace=VideoTrace(diagnostics_dir)
            except OSError as exc:self._stats['diagnostics_error']=str(exc)[:300]
            if self._trace is not None:
                try:
                    from video_packet_trace import VideoPacketTrace
                    self._packet_trace=VideoPacketTrace(self._trace.stream_path.with_suffix('.udp.bin'))
                except (OSError,ValueError,TypeError) as exc:self._stats['packet_trace_error']=str(exc)[:300]
        self._worker = threading.Thread(target=self._run, name='video-decode', daemon=True)
        self._worker.start()

    def submit(self, raw):
        if self._stop.is_set():
            return False
        at=time.time()
        try:
            self._queue.put_nowait(bytes(raw))
            self._stats['received_packets'] += 1
            self._last_receive = time.monotonic()
            accepted=True
        except queue.Full:
            self._stats['queue_drops'] += 1
            accepted=False
        if self._packet_trace is not None and not self._packet_trace_failed:
            try:self._packet_trace.submit(raw,accepted,at=at)
            except (OSError,ValueError) as exc:
                self._stats['packet_trace_error']=str(exc)[:300]
                self._packet_trace.error=str(exc)[:300]
                self._packet_trace_failed=True
        return accepted

    def _run(self):
        try:
            decoder = HEVCDecoder()
        except Exception as exc:
            self._stats['last_error'] = str(exc)
            self._stats['decode_errors'] += 1
            self._close_trace()
            return
        while not self._stop.is_set():
            try:
                raw = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            payload=None
            images=[]
            trace_record=None
            try:
                payload = self._assembler.feed(raw)
                if payload is None:
                    continue
                self._stats['assembled_frames'] += 1
                self._last_assembly = time.monotonic()
                if self._trace is not None:
                    try:trace_record=self._trace.begin(payload)
                    except OSError as exc:
                        self._stats['diagnostics_error']=str(exc)[:300]
                        self._close_trace()
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
                if payload is not None and self._trace is not None:
                    try:
                        self._trace.complete(trace_record,dict(decoded=len(images),warnings=getattr(decoder,'warning_count',0),
                            last_warning=getattr(decoder,'last_warning',None),errors=self._stats['decode_errors'],
                            dropped=self._assembler.dropped_frames))
                    except OSError as exc:
                        self._stats['diagnostics_error']=str(exc)[:300]
                        self._close_trace()
                self._queue.task_done()
        self._close_trace()

    def _close_trace(self):
        if self._trace is not None:
            try:self._trace.close()
            except OSError as exc:self._stats['diagnostics_error']=str(exc)[:300]

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
                        diagnostics=None if self._trace is None else self._trace.status(),
                        packet_diagnostics=None if self._packet_trace is None else self._packet_trace.status(),
                        last_receive_age_s=receive_age, last_assembly_age_s=assembly_age, preview_url=self._url,
                        queued_packets=self._queue.qsize(),
                        dropped_frames=self._assembler.dropped_frames,
                        invalid_packets=self._assembler.invalid_packets,
                        duplicate_packets=self._assembler.duplicate_packets,
                        worker_alive=self._worker.is_alive(), closed=self._stop.is_set())

    def set_subject_provider(self,provider):
        """Read-only snapshots are requested by HTTP handlers, never the receive thread."""
        if provider is not None and not callable(provider):raise TypeError('subject provider must be callable')
        self._subject_provider=provider

    def subject_preview(self):
        from subject_preview import preview_boxes
        provider=self._subject_provider
        if provider is None:return dict(boxes=[],lock_verified=False)
        try:return preview_boxes(provider())
        except Exception:return dict(boxes=[],lock_verified=False,unavailable=True)

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
                elif path == '/subjects':
                    body, mime, code = json.dumps(receiver.subject_preview()).encode(), 'application/json', 200
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
        if self._packet_trace is not None:
            self._packet_trace.close()
