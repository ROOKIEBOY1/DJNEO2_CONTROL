"""Offline NIC/socket/queue/HEVC comparison; no aircraft or network operations."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
from unittest.mock import patch

from outer import parse_outer
from video_packet_trace import packet_records
from video_stream import VideoAssembler


def digest(raw):return hashlib.sha256(raw).hexdigest()


def is_video(raw):
    try:return parse_outer(raw).channel==2 and len(raw)>20
    except (ValueError,IndexError):return False


def extract_drone_udp(data):
    # pktmon can retain an 802.11/SNAP prefix despite an Ethernet interface
    # declaration. Validate full IPv4/UDP bounds, addresses and outer protocol.
    for offset in range(min(128,max(0,len(data)-27))):
        if data[offset]>>4!=4:continue
        ihl=(data[offset]&15)*4
        if ihl<20 or offset+ihl+8>len(data) or data[offset+9]!=17:continue
        total=int.from_bytes(data[offset+2:offset+4],'big')
        if total<ihl+8 or offset+total>len(data):continue
        if int.from_bytes(data[offset+6:offset+8],'big')&0x3fff:continue
        if data[offset+12:offset+16]!=bytes((192,168,2,1)):continue
        src,dst,size,_=struct.unpack_from('!HHHH',data,offset+ihl)
        if src!=9003 or size<8 or size>total-ihl:continue
        raw=data[offset+ihl+8:offset+ihl+size]
        if is_video(raw):return raw
    return None


def nic_records(path):
    from pcapng import FileScanner,blocks
    with Path(path).open('rb') as source:
        for block in FileScanner(source):
            if isinstance(block,blocks.EnhancedPacket):
                raw=extract_drone_udp(block.packet_data)
                if raw is not None:yield float(block.timestamp),raw


def replay_assembly(rows):
    assembler=VideoAssembler()
    result=[]
    current=[0.]
    with patch('video_stream.time.monotonic',side_effect=lambda:current[0]):
        for at,accepted,raw in rows:
            if not accepted:continue
            current[0]=at
            payload=assembler.feed(raw)
            if payload is not None:result.append((at,digest(payload)))
    return result,dict(dropped_frames=assembler.dropped_frames,invalid_packets=assembler.invalid_packets,
                       duplicate_packets=assembler.duplicate_packets,pending_frames=assembler.pending_count)


def fragment_inventory(rows,horizon_s=2.):
    """Independent fragment accounting; a clue, not proof of framing semantics.

    A two-second grouping horizon deliberately differs from the live one-second
    assembler timeout. Bound groups to 128 and never join conflicting duplicates.
    """
    groups={};complete=[]
    stats=dict(complete_groups=0,incomplete_groups=0,conflicting_groups=0,
               invalid_fragments=0,missing_fragment_examples=[],grouping_horizon_s=horizon_s)
    def finish(key,g):
        first,last,count,parts,conflict=g
        if conflict:stats['conflicting_groups']+=1
        elif len(parts)==count:
            complete.append((last,digest(b''.join(parts[i] for i in range(count)))))
            stats['complete_groups']+=1
        else:
            stats['incomplete_groups']+=1
            if len(stats['missing_fragment_examples'])<20:
                stats['missing_fragment_examples'].append(dict(at=first,frame_id=key[1],expected=count,
                    missing_parts=[i for i in range(count) if i not in parts]))
    for at,accepted,raw in rows:
        if not accepted or not is_video(raw):continue
        count=raw[17]&127;part=((raw[18]&31)<<1)|(raw[17]>>7)
        if not 1<=count<=64 or part>=count:
            stats['invalid_fragments']+=1;continue
        for key,g in list(groups.items()):
            if at-g[0]>horizon_s:
                finish(key,g);del groups[key]
        key=(raw[2:4],raw[16],count)
        if key not in groups:
            if len(groups)>=128:
                oldest=next(iter(groups));finish(oldest,groups.pop(oldest))
            groups[key]=[at,at,count,{},False]
        g=groups[key];g[1]=at;payload=raw[20:]
        if part in g[3] and g[3][part]!=payload:g[4]=True
        g[3][part]=payload
    for key,g in groups.items():finish(key,g)
    return sorted(complete),stats


def compare_records(nic,received,assembled,quality,margin_s=1.):
    received=[r for r in received if is_video(r[2])]
    sessions={r[2][2:4] for r in received}
    nic=[r for r in nic if is_video(r[1]) and r[1][2:4] in sessions]
    complete=bool(quality.get('closed') is True and quality.get('queue_drops')==0
                  and quality.get('capped') is False and not quality.get('error'))
    hevc=quality.get('hevc_trace',{})
    assembly_complete=bool(hevc.get('closed') is True and hevc.get('capped') is False and not hevc.get('error'))
    report=dict(nic_video_packets=len(nic),application_packets=len(received),
                application_trace_complete=complete,application_trace_quality=quality,
                assembly_trace_complete=assembly_complete,
                overlap_valid=False,nic_not_in_application_count=0,application_not_in_nic_count=0,
                receiver_queue_rejections=0,replay_frames_not_in_actual_count=0,findings=[],
                limitations=[
                    '网卡观测不到的数据不能单独证明飞机未发或空口丢包；还需核对pktmon丢事件/覆盖范围。',
                    '对比使用共同时间窗口内的唯一原始包哈希，避免NIC多次观测同一包导致误判。',
                    '离线拼包按收包时间重放；工作线程排队时延可造成与实际超时判断不同，差异是排查线索而非已证明的代码缺陷。'])
    if not complete:report['findings'].append('程序收包诊断不完整；不能依据缺口判断丢失层级。')
    if not assembly_complete:report['findings'].append('拼包/解码记录不完整或已达上限，不能据此确定拼包缺口。')
    if not nic or not received:
        report['findings'].append('没有足够的网卡/程序同会话视频数据，无法确定丢失层级。')
        return report
    lo=max(min(r[0] for r in nic),min(r[0] for r in received))+margin_s
    hi=min(max(r[0] for r in nic),max(r[0] for r in received))-margin_s
    if hi<lo:
        report['findings'].append('共同采集时间太短或不重叠，无法确定丢失层级。')
        return report
    report.update(overlap_valid=True,overlap_start=lo,overlap_end=hi,edge_margin_s=margin_s)
    nic_hashes={digest(raw) for at,raw in nic if lo<=at<=hi}
    app_hashes={digest(raw) for at,accepted,raw in received if lo<=at<=hi}
    # Candidate timestamps use the common window; matching may cross its edge
    # because receive/worker timestamps naturally lag the NIC observation.
    nic_reference={digest(raw) for at,raw in nic}
    app_reference={digest(raw) for at,accepted,raw in received}
    missing=nic_hashes-app_reference
    report['nic_not_in_application_count']=len(missing)
    report['nic_not_in_application_examples']=sorted(missing)[:10]
    report['application_not_in_nic_count']=len(app_hashes-nic_reference)
    report['receiver_queue_rejections']=sum(not accepted for at,accepted,raw in received if lo<=at<=hi)
    replayed,stats=replay_assembly(received)
    independent,inventory=fragment_inventory(received)
    _,nic_inventory=fragment_inventory([(at,True,raw) for at,raw in nic])
    expected=Counter(h for at,h in replayed if lo<=at<=hi)
    actual=Counter(h for at,h in assembled if lo<=at<=hi)
    report['assembly_replay']=stats
    report['application_fragment_inventory']=inventory
    report['nic_fragment_inventory']=nic_inventory
    independent_hashes=Counter(h for at,h in independent if lo<=at<=hi)
    actual_reference=Counter(h for at,h in assembled)
    replay_reference=Counter(h for at,h in replayed)
    report['independent_complete_frames_not_in_actual_count']=sum((independent_hashes-actual_reference).values())
    report['replay_complete_frames_in_window']=sum(expected.values())
    report['actual_complete_frames_in_window']=sum(actual.values())
    report['replay_frames_not_in_actual_count']=sum((expected-actual_reference).values())
    report['actual_frames_not_in_replay_count']=sum((actual-replay_reference).values())
    if complete and missing:
        report['findings'].append('网卡已观测到的视频包未出现在完整程序记录中，重点核查系统收包到视频提交之间。')
    if report['application_not_in_nic_count']:
        report['findings'].append('程序收到的部分包未在网卡记录中出现，网卡捕获覆盖或事件丢失需先核实。')
    if report['receiver_queue_rejections']:
        report['findings'].append('已确认部分收到的视频包被程序视频队列拒绝。')
    if complete and assembly_complete and report['replay_frames_not_in_actual_count']:
        report['findings'].append('已接受的数据离线可拼成部分完整帧，但实际拼包输出未匹配，需核查队列处理与拼包超时。')
    if complete and assembly_complete and report['independent_complete_frames_not_in_actual_count']:
        report['findings'].append('独立分片清点找到未匹配的完整数据，需检查拼包超时及帧分组规则；这仍是候选线索。')
    if not report['findings']:
        report['findings'].append('共同窗口内未发现网卡到收包及拼包输出的缺口；继续核对源流参考帧是否缺失及解码阶段。')
    return report


def compare_files(pcap,raw_path):
    raw_path=Path(raw_path)
    summary=raw_path.with_suffix('.summary.json')
    quality=json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else {}
    # foo.udp.bin -> foo.hevc / foo.jsonl
    base=raw_path.with_suffix('').with_suffix('')
    stream=base.with_suffix('.hevc');index=base.with_suffix('.jsonl')
    hevc_summary=base.with_suffix('.hevc.summary.json')
    quality['hevc_trace']=json.loads(hevc_summary.read_text(encoding='utf-8')) if hevc_summary.exists() else {}
    assembled=[];progress=[]
    with stream.open('rb') as source:
        for line in index.read_text(encoding='utf-8').splitlines():
            row=json.loads(line);source.seek(row['offset']);payload=source.read(row['length'])
            if len(payload)!=row['length']:raise ValueError('HEVC diagnostic is truncated')
            assembled.append((row['at'],digest(payload)));progress.append(row)
    result=compare_records(list(nic_records(pcap)),list(packet_records(raw_path)),assembled,quality)
    result['sources']=dict(pcap=str(Path(pcap).resolve()),packets=str(raw_path.resolve()),hevc=str(stream.resolve()))
    result['decoder_progress']=dict(records=len(progress),decoded_frames=sum(r.get('decoded',0) for r in progress),
        final_warnings=progress[-1].get('warnings',0) if progress else 0,
        hevc_at_diagnostic_limit=quality['hevc_trace'].get('capped'))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pcap',type=Path,required=True)
    parser.add_argument('--raw',type=Path,required=True)
    args=parser.parse_args()
    result=compare_files(args.pcap,args.raw)
    output=args.pcap.with_suffix('.comparison.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved:',output)
    for finding in result['findings']:print(finding)
