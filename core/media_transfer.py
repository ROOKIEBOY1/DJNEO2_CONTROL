"""Capture-backed media extraction. No flight/camera commands; GET only.

Only Ethernet/IPv4/TCP is supported. Missing TCP bytes are reported, never
filled; HTTP messages after a gap are not guessed. Capture replay uses exact
observed GETs; the live-directory API accepts validated DUML directory items
and uses the same observed read-only HTTP shape.
"""
from collections import defaultdict
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
from urllib.parse import parse_qsl, urlsplit

HOST = '192.168.2.1'
MAX_MEDIA = 512 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tcp_packet(frame):
    if len(frame)<54 or frame[12:14]!=b'\x08\x00': return None
    ip=frame[14:]
    ihl=(ip[0]&15)*4; total=int.from_bytes(ip[2:4],'big')
    if (ip[0]>>4!=4 or ip[9]!=6 or ihl<20 or not ihl+20<=total<=len(ip)
            or int.from_bytes(ip[6:8],'big')&0x3fff): return None
    segment=ip[ihl:total]; thl=(segment[12]>>4)*4
    if not 20<=thl<=len(segment): return None
    sport,dport,seq=struct.unpack_from('!HHI',segment)
    address=lambda raw: '.'.join(map(str,raw))
    return dict(source=(address(ip[12:16]),sport),destination=(address(ip[16:20]),dport),
                sequence=seq,flags=segment[13],payload=segment[thl:])


def capture_http(source):
    """Reassemble only conversations involving the aircraft's fixed HTTP port."""
    from pcapng import FileScanner,blocks
    flows=defaultdict(lambda: {'request':[],'response':[]})
    generation=defaultdict(int); syns={}
    ignored=0
    with Path(source).open('rb') as stream:
        for block in FileScanner(stream):
            if not isinstance(block,blocks.EnhancedPacket): continue
            interface=block.section.interfaces[block.interface_id]
            packet=tcp_packet(block.packet_data) if interface.link_type==1 else None
            if not packet: continue
            if packet['destination']==(HOST,80):
                client=packet['source']; direction='request'
                if packet['flags']&2 and not packet['flags']&16:
                    if client in syns and syns[client]!=packet['sequence']:
                        generation[client]+=1
                    syns[client]=packet['sequence']
            elif packet['source']==(HOST,80):
                client=packet['destination']; direction='response'
            else:
                ignored+=1; continue
            if packet['payload']:
                seq=(packet['sequence']+bool(packet['flags']&2))%2**32
                flows[(*client,generation[client])][direction].append((seq,packet['payload']))
    exchanges,diagnostics=[],[]
    for flow,directions in sorted(flows.items()):
        parsed={}; detail=dict(flow=list(flow))
        for direction,segments in directions.items():
            merged=reassemble(segments)
            parsed[direction],error=parse_http(merged.pop('data'),response=direction=='response',
                                                allow_partial=direction=='response')
            chunks=merged.pop('chunks',[])
            if direction=='response':
                for reply in parsed[direction]:
                    start=reply['body_start']; end=start+reply['expected_length']
                    reply['captured_ranges']=[]
                    for offset,chunk in chunks:
                        lo=max(start,offset); hi=min(end,offset+len(chunk))
                        if lo<hi:
                            reply['captured_ranges'].append((lo-start,chunk[lo-offset:hi-offset]))
            detail[direction]=dict(merged,error=error,messages=len(parsed[direction]))
        diagnostics.append(detail)
        # Responses correspond in order on each non-pipelined HTTP/1.1 connection.
        for request,reply in zip(parsed['request'],parsed['response']):
            if request['headers'].get('host')!=HOST:
                raise ValueError('unexpected HTTP Host in aircraft conversation')
            exchanges.append(dict(flow=list(flow),request=request,response=reply))
    return exchanges,dict(flows=diagnostics,other_tcp_packets=ignored)


def collect_originals(exchanges):
    groups=defaultdict(list)
    for exchange in exchanges:
        request,reply=exchange['request'],exchange['response']
        path=media_path(request['target'])
        if not path.startswith('DCIM/') or not path.lower().endswith(('.jpg','.mp4')): continue
        if reply.get('complete',True):
            start,end,total=response_range(reply)
        else:
            # The complete HTTP header is captured even when TCP body bytes are missing.
            if reply['status']==206:
                match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',reply['headers'].get('content-range',''))
                if not match: raise ValueError('invalid partial response Content-Range')
                start,last,total=map(int,match.groups()); end=last+1
                if not 0<=start<end<=total<=MAX_MEDIA or end-start!=reply['expected_length']:
                    raise ValueError('partial Content-Range length mismatch')
            elif reply['status']==200:
                start,end,total=0,reply['expected_length'],reply['expected_length']
            else: raise ValueError('unsupported partial status')
        requested=request['headers'].get('range')
        if (requested and requested!=f'bytes={start}-{end-1}') or (not requested and reply['status']!=200):
            raise ValueError('request Range does not match response')
        ranges=reply.get('captured_ranges',[(0,reply['body'])])
        groups[(request['target'],reply['headers'].get('etag'),total)].append((start,end,ranges,requested))
    entries,bodies=[],{}
    for (target,etag,total),parts in sorted(groups.items(),key=lambda item: str(item[0])):
        joined=reassemble([(start+offset,body) for start,end,ranges,rng in parts for offset,body in ranges])
        parts.sort(key=lambda part: part[0])
        first_capture=min((start+offset for start,end,ranges,rng in parts for offset,body in ranges),default=-1)
        complete=first_capture==0 and not joined['gaps'] and len(joined['data'])==total
        covered=0
        for start,end,ranges,rng in parts:
            if start>covered: break
            covered=max(covered,end)
        captured_until=0; missing=[]
        for start,body in sorted((start+offset,body) for start,end,ranges,rng in parts for offset,body in ranges):
            if start>captured_until: missing.append((captured_until,start))
            captured_until=max(captured_until,start+len(body))
        if captured_until<total: missing.append((captured_until,total))
        entry=dict(name=PurePosixPath(media_path(target)).name,target=target,etag=etag,size=total,
                   complete=complete,replayable=covered==total,
                   captured_unique_bytes=joined['captured_bytes'],gaps=joined['gaps'],missing_ranges=missing)
        seen=set(); requests=[]
        for start,end,ranges,rng in parts:
            key=(start,end)
            if key in seen: continue
            seen.add(key)
            requests.append(dict(target=target,range=rng,start=start,end=end,length=end-start,
                                 known_ranges=[dict(start=offset,end=offset+len(part),sha256=digest(part))
                                               for offset,part in ranges]))
        entry['requests']=requests
        if complete:
            body=joined['data']; entry['sha256']=digest(body); bodies[entry['sha256']]=body
        entries.append(entry)
    return entries,bodies


def extract_capture(source, output):
    """Create new evidence directory, originals, and a JSON replay manifest."""
    output=Path(output)
    if output.exists(): raise FileExistsError(output)
    exchanges,diagnostics=capture_http(source)
    entries,bodies=collect_originals(exchanges)
    output.mkdir(parents=True)
    for entry in entries:
        if entry['complete']:
            entry['validation']=save_original(output/entry['name'],bodies[entry['sha256']])
    summary=[]
    for exchange in exchanges:
        request,reply=exchange['request'],exchange['response']
        summary.append(dict(flow=exchange['flow'],target=request['target'],range=request['headers'].get('range'),
                            status=reply['status'],headers=reply['headers'],body_bytes=len(reply['body']),
                            body_sha256=digest(reply['body']),complete=reply.get('complete',True),
                            expected_length=reply.get('expected_length',len(reply['body']))))
    manifest=dict(schema=1,host=HOST,source=str(Path(source).resolve()),
                  source_sha256=digest(Path(source).read_bytes()),
                  directory_list_api_observed=False,entries=entries,exchanges=summary,diagnostics=diagnostics)
    with (output/'manifest.json').open('x',encoding='utf-8') as stream:
        json.dump(manifest,stream,indent=2,ensure_ascii=False)
    return manifest


def reassemble(segments):
    """Return contiguous prefix and all gap intervals; validate every overlap.

    TCP sequence numbers are unwrapped within a <2 GiB capture window.
    Reassembly uses intervals, avoiding a Python object for every byte.
    """
    if not segments:
        return dict(data=b'', gaps=[], duplicate_bytes=0, captured_bytes=0,chunks=[])
    anchor = segments[0][0]
    ordered = sorted((anchor + ((seq-anchor+2**31) % 2**32)-2**31, payload)
                     for seq,payload in segments if payload)
    chunks, gaps, duplicates, origins = [], [], 0, []
    base = end = None
    current = bytearray()
    for start,payload in ordered:
        if base is None:
            base, end = start, start
            origins.append(start)
        if start > end:
            chunks.append(bytes(current)); current = bytearray()
            gaps.append((end,start)); base = end = start
            origins.append(start)
        overlap = min(len(payload), end-start)
        if overlap:
            if current[start-base:start-base+overlap] != payload[:overlap]:
                raise ValueError('TCP retransmission conflict')
            duplicates += overlap
        current.extend(payload[overlap:])
        end = max(end,start+len(payload))
    if current:
        chunks.append(bytes(current))
    return dict(data=chunks[0] if chunks else b'', gaps=gaps,
                duplicate_bytes=duplicates, captured_bytes=sum(map(len,chunks)),
                chunks=[(origin-origins[0],chunk) for origin,chunk in zip(origins,chunks)])


def parse_http(data, *, response=False, allow_partial=False):
    """Strict Content-Length framing; return preceding complete messages + error."""
    messages, offset = [], 0
    try:
        while offset < len(data):
            end = data.find(b'\r\n\r\n',offset)
            if end < 0:
                raise ValueError('incomplete HTTP header')
            lines = data[offset:end].decode('iso-8859-1').split('\r\n')
            first, headers = lines[0], {}
            for line in lines[1:]:
                key, sep, value = line.partition(':')
                key = key.lower()
                if not sep or not re.fullmatch(r'[a-z0-9-]+',key):
                    raise ValueError('invalid HTTP header')
                if key in headers:
                    raise ValueError('duplicate HTTP header')
                headers[key] = value.strip()
            if 'transfer-encoding' in headers:
                raise ValueError('unsupported Transfer-Encoding')
            if response:
                match = re.fullmatch(r'HTTP/1\.[01] (\d{3}) .*',first)
                if not match:
                    raise ValueError('invalid HTTP response')
                status = int(match[1])
                if status not in (204,304) and 'content-length' not in headers:
                    raise ValueError('response without Content-Length')
                item = dict(status=status)
            else:
                match = re.fullmatch(r'GET ([^\s]+) HTTP/1\.[01]',first)
                if not match:
                    raise ValueError('unsupported/non-GET HTTP request')
                item = dict(method='GET',target=match[1])
            length_text = headers.get('content-length','0')
            if not re.fullmatch(r'\d+',length_text):
                raise ValueError('invalid Content-Length')
            length = int(length_text)
            if length > MAX_MEDIA:
                raise ValueError('HTTP body exceeds limit')
            start = end+4
            item.update(headers=headers,body=data[start:min(start+length,len(data))],
                        body_start=start,expected_length=length,complete=start+length<=len(data))
            if start+length > len(data):
                if allow_partial: messages.append(item)
                raise ValueError(f'incomplete HTTP body: expected {length}, captured {len(data)-start}')
            messages.append(item)
            offset = start+length
    except ValueError as exc:
        return messages, str(exc)
    return messages, None


def media_path(target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != '/v2':
        raise ValueError('only observed /v2 media targets allowed')
    query = parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)
    if len(query)!=2 or [k for k,v in query]!=['storage','path'] or query[0][1]!='1':
        raise ValueError('unobserved media query parameters')
    path = query[1][1]
    if (not re.fullmatch(r'(?:DCIM|MISC/THM)/[A-Za-z0-9_./-]+',path)
            or any(part in ('','.','..') for part in path.split('/'))
            or PurePosixPath(path).suffix.lower() not in ('.jpg','.mp4','.thm','.scr')):
        raise ValueError('unsafe media path')
    return path


def response_range(message):
    length = len(message['body'])
    if message['status']==200:
        return 0,length,length
    if message['status']!=206:
        raise ValueError('HTTP media status must be 200 or 206')
    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',message['headers'].get('content-range',''))
    if not match:
        raise ValueError('invalid Content-Range')
    start,end,total = map(int,match.groups())
    if not 0 <= start <= end < total <= MAX_MEDIA or end-start+1 != length:
        raise ValueError('Content-Range length mismatch')
    return start,end+1,total


def validate_media(data, suffix):
    if suffix.lower()=='.jpg':
        eoi=data.rfind(b'\xff\xd9')
        if not data.startswith(b'\xff\xd8') or eoi<2:
            raise ValueError('invalid/truncated JPEG markers')
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
                if picture.format not in ('JPEG','MPO'):
                    raise ValueError('not JPEG')
                width,height=picture.size
                exif=picture.getexif()
                camera=dict(make=exif.get(271),model=exif.get(272),datetime=exif.get(306))
                frames=getattr(picture,'n_frames',1)
                for frame in range(frames): picture.seek(frame); picture.load()
                return dict(format=picture.format,width=width,height=height,frames=frames,
                            trailing_metadata_bytes=len(data)-eoi-2,exif=camera)
        except (OSError,SyntaxError) as exc:
            raise ValueError('invalid JPEG image') from exc
    if suffix.lower()!='.mp4':
        raise ValueError('only original JPEG/MP4 supported')
    boxes,offset = [],0
    while offset < len(data):
        if offset+8>len(data):
            raise ValueError('truncated MP4 box header')
        size,kind = struct.unpack_from('>I4s',data,offset)
        header=8
        if size==1:
            if offset+16>len(data): raise ValueError('truncated MP4 extended size')
            size=struct.unpack_from('>Q',data,offset+8)[0]; header=16
        if size==0: size=len(data)-offset
        if size<header or offset+size>len(data):
            raise ValueError('truncated/invalid MP4 box')
        boxes.append(dict(type=kind.decode('ascii',errors='replace'),offset=offset,size=size))
        offset+=size
    kinds=[box['type'] for box in boxes]
    if not kinds or kinds[0]!='ftyp' or not {'moov','mdat'}.issubset(kinds):
        raise ValueError('MP4 missing ftyp/moov/mdat')
    return dict(format='MP4',boxes=boxes)


def _check_download_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError('media download cancelled')


def save_original(destination, data, *, cancel_event=None):
    _check_download_cancel(cancel_event)
    destination=Path(destination)
    if destination.exists(): raise FileExistsError(destination)
    validation=validate_media(data,destination.suffix)
    _check_download_cancel(cancel_event)
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=destination.name+'.',suffix='.part',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        # Hard-link publication is atomic and fails when destination exists.
        _check_download_cancel(cancel_event)
        os.link(temp,destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    return dict(size=len(data),sha256=digest(data),**validation)


def download_entry(entry, destination, *, connect=False):
    if not connect:
        raise ValueError('network disabled; use --connect explicitly')
    destination=Path(destination)
    if destination.exists(): raise FileExistsError(destination)
    if not entry.get('replayable') or not entry.get('requests'):
        raise ValueError('only captured GETs covering the full original may be replayed')
    if entry['size']>MAX_MEDIA: raise ValueError('media exceeds size limit')
    chunks=[]
    # HTTPConnection directly opens the fixed IP; no proxy or redirects.
    for request in entry['requests']:
        if PurePosixPath(media_path(request['target'])).name!=entry['name']:
            raise ValueError('request/name mismatch')
        headers={'Accept':'*/*','Content-Type':'text/plain'}
        if request.get('range'): headers['Range']=request['range']
        conn=http.client.HTTPConnection(HOST,80,timeout=30)
        try:
            conn.request('GET',request['target'],headers=headers)
            reply=conn.getresponse()
            reply_headers={}
            for key,value in reply.getheaders():
                key=key.lower()
                if key in reply_headers: raise ValueError('duplicate response header')
                reply_headers[key]=value
            if 'transfer-encoding' in reply_headers or reply_headers.get('content-encoding','identity')!='identity':
                raise ValueError('encoded response unsupported')
            if reply_headers.get('content-length')!=str(request['length']):
                raise ValueError('download Content-Length mismatch')
            body=reply.read(request['length']+1)
            item=dict(status=reply.status,headers=reply_headers,body=body)
            start,end,total=response_range(item)
            if (start,end,total)!=(request['start'],request['end'],entry['size']):
                raise ValueError('download range mismatch')
            if entry.get('etag') and reply_headers.get('etag')!=entry['etag']:
                raise ValueError('download ETag differs from capture')
            for known in request['known_ranges']:
                if digest(body[known['start']:known['end']])!=known['sha256']:
                    raise ValueError('download differs from captured bytes')
            chunks.append((start,body))
        finally:
            conn.close()
    joined=reassemble(chunks)
    if (joined['gaps'] or len(joined['data'])!=entry['size']
            or entry.get('sha256') and digest(joined['data'])!=entry['sha256']):
        raise ValueError('incomplete or changed original')
    return save_original(destination,joined['data'])


def download_directory_item(item, destination, *, connect=False, cancel_event=None):
    """Download a validated live directory item via observed media GET syntax.

    The caller supplies an item returned by the actual DUML album parser.
    This API has no historical body hash or ETag: it checks directory size,
    current response ETag consistency, HTTP framing and media structure only.
    JPEG uses one GET; video follows the observed five-range partition.
    """
    if not connect:
        raise ValueError('network disabled; use connect=True explicitly')
    _check_download_cancel(cancel_event)
    destination=Path(destination)
    if destination.exists(): raise FileExistsError(destination)
    if not isinstance(item,dict): raise ValueError('invalid directory item')
    name,path,size,kind=(item.get(key) for key in ('name','path','size','kind'))
    if not isinstance(name,str) or not re.fullmatch(r'DJI_[0-9]{14}_[0-9]{4}_D\.(?:JPG|MP4|jpg|mp4)',name):
        raise ValueError('invalid directory media name')
    if path!='DCIM/DJI_001/'+name:
        raise ValueError('directory path/name mismatch')
    suffix=PurePosixPath(name).suffix.lower()
    if kind!=('photo' if suffix=='.jpg' else 'video'):
        raise ValueError('directory kind/suffix mismatch')
    if type(size) is not int or not 1<=size<=MAX_MEDIA or suffix=='.mp4' and size<5:
        raise ValueError('invalid directory media size')
    if destination.suffix.lower()!=suffix:
        raise ValueError('destination suffix does not match directory item')
    # Only the extension changes case, matching observed aircraft HTTP paths.
    canonical_path=path[:-len(suffix)]+suffix
    target='/v2?storage=1&path='+canonical_path
    media_path(target)
    if suffix=='.mp4':
        step=size//5
        intervals=[(index*step,(index+1)*step if index<4 else size) for index in range(5)]
    else:
        intervals=[(0,size)]
    data=bytearray(); current_etag=None
    for start,end in intervals:
        _check_download_cancel(cancel_event)
        headers={'Accept':'*/*','Content-Type':'text/plain'}
        if suffix=='.mp4': headers['Range']=f'bytes={start}-{end-1}'
        connection=http.client.HTTPConnection(HOST,80,timeout=5)
        try:
            connection.request('GET',target,headers=headers)
            _check_download_cancel(cancel_event)
            reply=connection.getresponse()
            reply_headers={}
            for key,value in reply.getheaders():
                key=key.lower()
                if key in reply_headers: raise ValueError('duplicate response header')
                reply_headers[key]=value
            if reply.status!=(206 if suffix=='.mp4' else 200):
                raise ValueError('unexpected media HTTP status; redirects are not followed')
            if ('transfer-encoding' in reply_headers
                    or reply_headers.get('content-encoding','identity')!='identity'):
                raise ValueError('encoded media response unsupported')
            if reply_headers.get('content-length')!=str(end-start):
                raise ValueError('directory download Content-Length mismatch')
            if suffix=='.mp4':
                if reply_headers.get('content-range')!=f'bytes {start}-{end-1}/{size}':
                    raise ValueError('directory download Content-Range mismatch')
            elif 'content-range' in reply_headers:
                raise ValueError('unexpected Content-Range on whole JPEG')
            etag=reply_headers.get('etag')
            if not isinstance(etag,str) or not re.fullmatch(r'"[^"\r\n]+"',etag):
                raise ValueError('missing/invalid strong media ETag')
            if current_etag is not None and etag!=current_etag:
                raise ValueError('media ETag changed between ranges')
            current_etag=etag
            remaining=end-start
            while remaining:
                _check_download_cancel(cancel_event)
                chunk=reply.read(min(64*1024,remaining))
                _check_download_cancel(cancel_event)
                if not chunk or len(chunk)>remaining:
                    raise ValueError('incomplete/oversized directory media response')
                data.extend(chunk); remaining-=len(chunk)
        finally:
            connection.close()
    _check_download_cancel(cancel_event)
    if len(data)!=size: raise ValueError('directory original length mismatch')
    result=save_original(destination,bytes(data),cancel_event=cancel_event)
    result.update(etag=current_etag,source_path=canonical_path,historical_hash_verified=False)
    return result
