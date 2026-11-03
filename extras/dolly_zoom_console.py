"""Native dolly zoom PC console. No arguments: offline help, no connection."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
import json
from pathlib import Path
import queue
import threading
import time
from dolly_zoom_protocol import FRAMING
from native_dolly_zoom import NativeDollyZoom

HELP='命令：景别 半身/全身/远景 | 倍率 2/3 | 渐远 开/关 | 准备 | 滑动变焦 | 确认 | 停止 | 降落 | 状态 | 主体 | 退出'

class ConsoleActions:
    def __init__(self,mission,emit=print):
        self.mission=mission
        self.emit=emit
        self.config=dict(framing='半身',zoom=2,retreat=True)
        self.pending_until=None
        self.worker=None
        self.token=threading.Event()

    def cancel(self):
        self.pending_until=None
        self.token.set()

    def _run(self,action):
        self.token=threading.Event()
        token=self.token
        config=self.config.copy()
        def execute():
            try:
                if action=='prepare':
                    result=self.mission.prepare(**config,cancel_event=token)
                    if not token.is_set():self.emit('准备完成：参数回显、相机录像模式已核验。'+str(result))
                else:
                    reply=self.mission.start(confirmed=True,cancel_event=token)
                    if token.is_set():self.emit('启动等待已取消；请观察飞机及停止状态。')
                    else:self.emit('启动请求回复：'+('未收到；任务仍可能已启动' if reply is None else reply.hex())
                                   +'；回复不等于执行完成，可随时输入停止或降落。')
            except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
                self.emit('操作未完成：'+str(exc))
        self.worker=threading.Thread(target=execute,daemon=True)
        self.worker.start()

    def handle(self,text):
        text=text.strip()
        compact=''.join(text.split())
        if compact=='退出':self.cancel();return False
        if compact in ('停止','降落'):
            self.cancel()
            try:
                reply=self.mission.stop(timeout=.4) if compact=='停止' else self.mission.land()
                self.emit(compact+'请求回复：'+('未收到' if reply is None else reply.hex())+'；请核对实际表现。')
            except (OSError,RuntimeError,TimeoutError) as exc:self.emit(compact+'未确认：'+str(exc))
            return True
        if compact in ('状态','主体'):
            status=self.mission.status()
            value=status if compact=='状态' else status['aircraft'].get('subject_observation',{})
            self.emit(json.dumps(value,ensure_ascii=False,indent=2))
            return True
        if self.worker is not None and self.worker.is_alive():
            self.emit('上一操作正在处理中，未排队；可输入停止或降落。');return True
        changed=False
        if compact.startswith('景别'):
            value=compact[2:]
            if value not in FRAMING:self.emit('景别只支持半身、全身、远景');return True
            self.config['framing']=value;changed=True
        elif compact.startswith('倍率'):
            value=compact[2:].lower().removesuffix('x').removesuffix('×')
            if value not in ('2','3'):self.emit('倍率只支持2或3（范围1–2× / 1–3×）');return True
            self.config['zoom']=int(value);changed=True
        elif compact.startswith('渐远'):
            value=compact[2:]
            if value not in ('开','关'):self.emit('渐远只支持开或关');return True
            self.config['retreat']=value=='开';changed=True
        if changed:
            self.pending_until=None;self.mission.prepared=False
            self.emit('设置已修改，尚未发送；请输入准备。'+str(self.config));return True
        if compact=='准备':
            self.pending_until=None;self._run('prepare')
        elif compact in ('滑动变焦','启动'):
            if not self.mission.prepared:self.emit('请先输入准备。');return True
            self.pending_until=time.monotonic()+10
            self.emit('将启动原生滑动变焦，可能自动起飞；相机应看到人物。10秒内输入确认。')
        elif compact=='确认':
            pending,self.pending_until=self.pending_until,None
            if pending is None or time.monotonic()>pending:self.emit('没有有效的待确认启动请求。')
            else:self._run('start')
        else:self.emit(HELP)
        return True

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect',action='store_true')
    parser.add_argument('--video',action='store_true')
    args=parser.parse_args()
    if args.video and not args.connect:parser.error('--video需要同时使用--connect')
    if not args.connect:
        print('离线预览：未连接飞机。加入 --connect 后开启PC滑动变焦；--video开启本地视频。')
        print('默认：半身、1–2×、渐远开、录像。只从地面启动，每次连接执行一轮任务。')
        print('准备 → 滑动变焦 → 确认；停止后悬停，降落需单独输入。退出会尝试停止和降落。')
        print(HELP);return
    from flight_controller import FlightController,detect_local_ip,PROFILE
    from routing_sequences import RoutingSequenceStore,historical_next_counter
    base=Path(__file__).resolve().parents[1]
    routing=controller=video=mission=actions=None
    events=queue.Queue()
    try:
        if args.video:
            from video_stream import VideoReceiver
            video=VideoReceiver(preview=True,diagnostics_dir=base/'captures'/'video-diagnostics')
            print('相机预览地址：',video.start())
            print('视频诊断已启用：captures/video-diagnostics（每次最多128 MiB）')
        routing=RoutingSequenceStore(initial=lambda:historical_next_counter(base/'captures'))
        controller=FlightController.connect(detect_local_ip(),reliable=True,tick_interval=.05,
            registration_profile=PROFILE,routing_sequences=routing,video_receiver=video)
        if not controller.initialize_control_link():raise RuntimeError('设备或新鲜视频未确认，未开放任务控制')
        mission=NativeDollyZoom(controller)
        actions=ConsoleActions(mission)
        print('初始化完成。请让飞机保持落地，并退出手机DJI Fly飞机连接及其他电脑控制台。')
        print('默认半身、1–2×、渐远开、录像；准备 → 滑动变焦 → 确认。主体由飞机自动识别。')
        print('先在安全空旷处短时试运行，输入停止确认悬停后再输入降落。'+HELP)
        def keyboard():
            while True:
                try:text=input().strip()
                except (EOFError,KeyboardInterrupt):text='退出'
                events.put(text)
                if text=='退出':return
        threading.Thread(target=keyboard,daemon=True).start()
        previous=None
        while True:
            observation=controller.status()['subject_observation']
            task=observation['task']
            current=(None if task is None else task['payload_hex'],observation['candidate_count'])
            if current!=previous:
                print('任务状态：',current[0] or '暂无新鲜数据','；候选主体：',current[1],
                      '（检测框不代表已核验锁定）')
                previous=current
            try:text=events.get(timeout=.25)
            except queue.Empty:continue
            if not actions.handle(text):break
    except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
        print('无法继续：',exc)
        raise SystemExit(1)
    except KeyboardInterrupt:print('正在结束任务会话……')
    finally:
        try:
            if actions is not None:actions.cancel()
            if mission is not None and controller.native_task_may_fly:
                try:
                    mission.land(timeout=.5)
                    print('退出时已尝试停止任务并降落，请目视确认落地。')
                except (OSError,ValueError,RuntimeError,TimeoutError) as exc:print('退出时停止/降落未确认：',exc)
            if actions is not None and actions.worker is not None:actions.worker.join(3)
        finally:
            try:
                if controller is not None:
                    controller.close()
                    print('会话记录：',controller.save_log())
            finally:
                try:
                    if video is not None:video.close()
                finally:
                    if routing is not None:routing.close()

if __name__=='__main__':main()
