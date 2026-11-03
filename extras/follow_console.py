"""Native Follow PC trial. Offline help by default; --connect enables sending."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import argparse
from collections import deque
import json
from pathlib import Path
import queue
import threading
import time
from native_follow import NativeFollow
from landing_confirmation import eligible_landing_confirmation
from landing_status import FEEDBACK

HELP = '命令：准备 | 跟随 | 确认 | 停止 | 降落 | 状态 | 主体 | 退出'


class ConsoleActions:
    def __init__(self, mission, emit=print):
        self.mission = mission
        self._emit = emit
        self.events = deque(maxlen=1000)
        self.pending_until = None
        self.worker = None
        self.token = threading.Event()
        self._landing_request = None
        self._last_landing_feedback = None
        self._link_problem = None

    def emit(self, message):
        self.events.append(dict(at=time.time(), message=str(message)[:4000]))
        self._emit(message)

    def cancel(self):
        self.pending_until = None
        self.token.set()
        self._landing_request = None

    def _run(self, action):
        self.token = threading.Event()
        token = self.token
        def execute():
            try:
                if action == 'prepare':
                    self.mission.prepare(cancel_event=token)
                    if not token.is_set(): self.emit('跟随模式完整回显已核对；可输入跟随。')
                else:
                    reply = self.mission.start(confirmed=True, cancel_event=token)
                    if token.is_set():
                        self.emit('启动等待已取消，请核对飞机和停止状态。')
                    else:
                        self.emit('跟随启动回复：'+('未收到，任务仍可能启动' if reply is None else reply.hex())
                                  +'；请观察实际跟随，可输入停止或降落。')
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                self.emit('操作未完成：'+str(exc))
        self.worker = threading.Thread(target=execute, daemon=True)
        self.worker.start()

    def handle(self, text):
        text = ''.join(text.split())
        self.events.append(dict(at=time.time(), input=text[:200]))
        if text == '退出':
            self.cancel()
            return False
        if text in ('停止', '停止跟随', '降落'):
            self.cancel()
            try:
                reply = self.mission.land() if text == '降落' else self.mission.stop(timeout=.4)
                if text == '降落' and reply == b'\0':
                    self._landing_request = self.mission.link.status().get('landing_observation', {}).get('requested_at')
                self.emit(text+'请求回复：'+('未收到' if reply is None else reply.hex())
                          +'；请观察是否悬停或落地，回复本身不代表动作完成。')
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                self.emit(text+'未确认：'+str(exc))
            return True
        if text in ('状态', '主体'):
            state = self.mission.status()
            self.emit(json.dumps(state if text == '状态' else state['aircraft'].get('subject_observation', {}),
                                 ensure_ascii=False, indent=2))
            return True
        if self.worker is not None and self.worker.is_alive():
            self.emit('上一操作正在处理中，未排队；可输入停止或降落。')
            return True
        if text == '准备':
            self.pending_until = None
            self._run('prepare')
        elif text in ('跟随', '启动跟随'):
            if not self.mission.prepared:
                self.emit('请先输入准备。')
                return True
            self.pending_until = time.monotonic()+10
            self.emit('跟随可能自动起飞，请让相机看到人物；10秒内输入确认。')
        elif text == '确认':
            pending, self.pending_until = self.pending_until, None
            if pending is None or time.monotonic() > pending:
                self.emit('没有有效的待确认跟随请求。')
            else:
                self._run('start')
        else:
            self.emit(HELP)
        return True

    def poll(self):
        state = self.mission.link.status()
        error = state.get('error')
        age = state.get('last_downlink_age_s')
        problem = ('控制连接已中断：'+str(error)+'；停止和降落可能无法送达，请用原厂方式接管。'
                   if error else '通信超过1秒未更新；无法确认停止或降落，请核对连接并准备原厂接管。'
                   if age is not None and age > 1 else None)
        if problem != self._link_problem:
            if problem:
                self.emit(problem)
            elif self._link_problem:
                self.emit('通信状态已有更新，请核对飞机实际状态。')
            self._link_problem = problem
        flight = state.get('landing_observation', {})
        feedback = FEEDBACK.get(flight.get('phase')) if flight.get('requested_at') is not None else None
        key = (flight.get('requested_at'), flight.get('phase'))
        if feedback and key != self._last_landing_feedback:
            self.emit(feedback)
        self._last_landing_feedback = key
        if self._landing_request is None:
            return
        if flight.get('requested_at') != self._landing_request:
            self._landing_request = None
            return
        if eligible_landing_confirmation(state):
            # One follow-up for this console's explicit, acknowledged land only.
            self._landing_request = None
            try:
                reply = self.mission.link.confirm_landing(confirmed=True, timeout=.2)
                self.emit('已按当前降落状态发送一次后续降落确认，回复：'
                          +('未收到' if reply is None else reply.hex())+'；继续观察实际落地。')
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                self.emit('后续降落确认未完成：'+str(exc)+'；请核对飞机状态。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', action='store_true')
    parser.add_argument('--video', action='store_true')
    args = parser.parse_args()
    if args.video and not args.connect:
        parser.error('--video需要同时使用--connect')
    if not args.connect:
        print('离线预览：未连接飞机。--connect 连接；--video 开启浏览器视频及主体框。')
        print('首版从地面启动，每次连接一轮：准备 → 跟随 → 确认 → 停止 → 降落。')
        print('跟随由飞机自动识别人；停止后预期悬停，降落需单独输入。')
        print(HELP)
        return
    from flight_controller import FlightController, detect_local_ip, PROFILE
    from routing_sequences import RoutingSequenceStore, historical_next_counter
    base = Path(__file__).resolve().parents[1]
    routing = controller = video = mission = actions = None
    events = queue.Queue()
    try:
        if args.video:
            from video_stream import VideoReceiver
            video = VideoReceiver(preview=True, diagnostics_dir=base/'captures'/'video-diagnostics')
            print('相机预览地址：', video.start())
        routing = RoutingSequenceStore(initial=lambda: historical_next_counter(base/'captures'))
        controller = FlightController.connect(detect_local_ip(), reliable=True, tick_interval=.05,
            registration_profile=PROFILE, routing_sequences=routing, video_receiver=video)
        if not controller.initialize_control_link():
            raise RuntimeError('设备或新鲜视频未确认，未开放跟随')
        mission = NativeFollow(controller)
        actions = ConsoleActions(mission)
        print('初始化完成。从地面开始，每次连接一轮；主体由飞机自动识别。')
        print('准备 → 跟随 → 确认；停止确认悬停后，再输入降落。'+HELP)
        def keyboard():
            while True:
                try: text = input().strip()
                except (EOFError, KeyboardInterrupt): text = '退出'
                events.put(text)
                if text == '退出': return
        threading.Thread(target=keyboard, daemon=True).start()
        previous = None
        while True:
            state = controller.status()
            observation = state['subject_observation']
            task = observation['task']
            current = (None if task is None else task['payload_hex'], observation['candidate_count'])
            if current != previous:
                print('跟随任务状态：', current[0] or '暂无新鲜数据', '；候选主体：', current[1])
                if current[0] == '050a000100': print('已收到跟随执行状态；请观察是否实际跟随。')
                if mission.started_at is not None and current[0] in ('0000000101', '0000000102'):
                    print('任务退出状态已收到；请目视确认悬停，降落需单独输入。')
                previous = current
            actions.poll()
            try: text = events.get(timeout=.25)
            except queue.Empty: continue
            if not actions.handle(text): break
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print('无法继续：', exc)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('正在结束跟随会话……')
    finally:
        try:
            if actions is not None: actions.cancel()
            if mission is not None and controller.native_task_may_fly:
                flight = controller.status().get('landing_observation', {})
                landed = (flight.get('telemetry_age_s') is not None and 0 <= flight['telemetry_age_s'] <= 1
                          and flight.get('motors_on') is False and flight.get('airborne_flag') is False)
                if not landed:
                    try:
                        mission.land(timeout=.5)
                        print('退出时已尝试停止并降落，尚未确认落地；请目视确认并准备原厂接管。')
                    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                        print('退出时停止/降落未确认：', exc)
            if actions is not None and actions.worker is not None: actions.worker.join(3)
        finally:
            try:
                if controller is not None:
                    controller.close()
                    output = controller.save_log()
                    if actions is not None:
                        try:
                            path = Path(output)
                            record = json.loads(path.read_text(encoding='utf-8'))
                            record['follow_console_events'] = list(actions.events)
                            temporary = path.with_suffix('.follow.tmp')
                            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                            temporary.replace(path)
                        except (OSError, ValueError) as exc:
                            print('控制台操作记录未能追加：', exc)
                    print('会话记录：', output)
            finally:
                try:
                    if video is not None: video.close()
                finally:
                    if routing is not None: routing.close()


if __name__ == '__main__':
    main()
