"""Offline replay of local indexed Follow traces; never opens sockets."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import sys,json,collections,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import av
from unittest.mock import patch
from video_stream import _TimingParameterFilter,_timing_signature,_LowDelayPictureOrder
from diagnose_dolly_video import nals
av.logging.set_level(av.logging.WARNING)
p=Path(sys.argv[1]); data=p.read_bytes(); records=[json.loads(l) for l in p.with_suffix('.jsonl').read_text().splitlines()]
for mode in sys.argv[2:] or ['timing']:
 c=av.CodecContext.create('hevc','r');c.thread_count=1; f=_TimingParameterFilter(); order=_LowDelayPictureOrder(); stats=collections.Counter();active={};events=[];buckets={};last=None
 for i,r in enumerate(records):
  payload=data[r['offset']:r['offset']+r['length']]; elapsed=r['at']-records[0]['at'];b=buckets.setdefault(int(elapsed//10),collections.Counter())
  for prefix,nal in nals(payload):
   k=(nal[0]>>1)&63
   if k in (32,33,34):
    h=hashlib.sha256(nal).hexdigest()[:12]
    if active.get(k)!=h:
     try:sig=hashlib.sha256(str(_timing_signature(nal)).encode()).hexdigest()[:12]
     except Exception as e:sig=str(e)
     events.append([i,round(elapsed,2),k,h,sig,nal.hex()]);active[k]=h
  if mode in ('timing','timingpacket','reordered'):payload=f.feed(payload)
  if mode=='reordered':
   with patch('video_stream.time.monotonic',return_value=r['at']):payload=order.feed(payload)
  if mode=='firstparams':
   pieces=[]
   for pref,nal in nals(payload):
    k=(nal[0]>>1)&63
    if k in (32,33,34):
     if stats['seen'+str(k)]:continue
     stats['seen'+str(k)]+=1
    pieces.append(pref)
   payload=b''.join(pieces)
  if not payload:continue
  with av.logging.Capture(local=True) as msgs:
   try:
    packets=[av.Packet(payload)] if mode in ('packet','timingpacket') else c.parse(payload)
    for packet in packets:
     for fr in c.decode(packet):
      if fr.is_corrupt: stats['corrupt']+=1;continue
      stats['decoded']+=1;b['decoded']+=1;last=elapsed
   except Exception as e:stats['errors']+=1
  stats['warnings']+=len(msgs);b['warnings']+=len(msgs)
 result=dict(mode=mode,stats=stats,last=last,duration=elapsed,buckets=buckets,params=events,suppressed=f.suppressed,reordered=order.reordered)
 out=p.with_suffix('.'+mode+'.follow.json');out.write_text(json.dumps(result,indent=2));print(json.dumps(dict(output=str(out),**{k:result[k] for k in ['mode','stats','last','duration','suppressed','reordered']})))
