"""Administrator-only paired NIC/application diagnostic; flight remains interactive."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
import ctypes
import datetime
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

BASE=Path(__file__).resolve().parents[1]


def decode_output(raw):
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('mbcs' if sys.platform=='win32' else 'gb18030',errors='replace')


def checked(args):
    result=subprocess.run(args,capture_output=True)
    text=decode_output(result.stdout)+decode_output(result.stderr)
    if result.returncode:raise RuntimeError(' '.join(args)+'\n'+text)
    return text


def require_idle(text):
    if not any(value in text.lower() for value in ('not running','not started','stopped','未运行','未在运行','已停止','未启动','尚未启动')):
        raise RuntimeError('未确认 pktmon 空闲，未启动采集；不会停止其他抓包。状态：'+text)


def capture_trial(captures=BASE/'captures',command=checked,console=None):
    zone=datetime.timezone(datetime.timedelta(hours=8))
    stamp=datetime.datetime.now(zone).strftime('%Y%m%d-%H%M%S')
    token=secrets.token_hex(3)
    folder=Path(captures)/('video-layer-'+stamp+'-'+token)
    folder.mkdir(parents=True,exist_ok=False)
    etl=folder/'nic.etl';pcap=folder/'nic.pcapng'
    filter_name='neo-video-'+token
    report=dict(started_at=time.time(),folder=str(folder.resolve()),etl=str(etl.resolve()),
                pcap=str(pcap.resolve()),filter_name=filter_name,errors=[],
                nic_limit_mib=512,nic_log_mode='circular',capture_stop_confirmed=False)
    before=set((Path(captures)/'video-diagnostics').glob('*.udp.bin'))
    started=filtered=False
    try:
        status=command(['pktmon','status'])
        (folder/'pktmon-before.txt').write_text(status,encoding='utf-8')
        require_idle(status)
        command(['pktmon','filter','add',filter_name,'-i','192.168.2.1','-t','UDP','-p','9003'])
        filtered=True
        command(['pktmon','start','--capture','--comp','nics','--pkt-size','0',
                 '--file-name',str(etl.resolve()),'--file-size','512','--log-mode','circular'])
        started=True
        print('网卡采集已开始。飞行仍由你在控制台逐条操作；请落地后输入退出。',flush=True)
        print('诊断目录：',folder.resolve(),flush=True)
        if console is None:
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('follow_console.py')),'--connect','--video'])
            try:report['console_exit_code']=process.wait()
            except KeyboardInterrupt:
                print('请等待飞控控制台结束；不会强制终止控制进程。',flush=True)
                report['console_exit_code']=process.wait()
        else:
            report['console_exit_code']=console()
    finally:
        if started:
            try:
                text=command(['pktmon','stop'])
                report['capture_stop_confirmed']=True
                (folder/'pktmon-stop.txt').write_text(text,encoding='utf-8')
                command(['pktmon','etl2pcap',str(etl.resolve()),'--out',str(pcap.resolve())])
                report['pcap_conversion_ok']=True
            except (OSError,RuntimeError) as exc:report['errors'].append(str(exc))
        if filtered:
            try:command(['pktmon','filter','remove',filter_name])
            except (OSError,RuntimeError) as exc:report['errors'].append(str(exc))
        after=set((Path(captures)/'video-diagnostics').glob('*.udp.bin'))
        report['application_packet_traces']=[str(p.resolve()) for p in sorted(after-before)]
        report['ended_at']=time.time()
        (folder/'session.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('配对采集记录：',folder/'session.json',flush=True)
    if report.get('pcap_conversion_ok') and len(report['application_packet_traces'])==1:
        try:
            from compare_video_layers import compare_files
            result=compare_files(pcap,Path(report['application_packet_traces'][0]))
            (folder/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print('逐层比对结果：',folder/'comparison.json',flush=True)
        except (OSError,ValueError,RuntimeError) as exc:
            report['errors'].append('比对未完成：'+str(exc))
            (folder/'session.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',action='store_true',help='启动采集和交互式跟随控制台')
    args=parser.parse_args()
    if not args.capture:
        print('离线帮助：未抓包、未连接飞机。请在管理员 PowerShell 中添加 --capture。')
        print('采集后正常操作：准备 → 跟随 → 确认 → 停止 → 降落，确认落地后退出。')
        return
    if sys.platform!='win32' or not ctypes.windll.shell32.IsUserAnAdmin():
        parser.exit(1,'网卡采集需要 Windows 管理员权限。请以管理员身份打开 PowerShell，切换到项目目录后重试。\n')
    try:capture_trial()
    except (OSError,RuntimeError) as exc:parser.exit(1,'采集未完成：'+str(exc)+'\n')


if __name__=='__main__':main()
