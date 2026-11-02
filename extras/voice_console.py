"""Offline Chinese voice control. No options prints help without microphone/network."""
import argparse
import datetime
import json
from pathlib import Path
import queue
import threading
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from voice_commands import ALIASES
from voice_control import VoiceExecutor
from voice_motion import CalibrationStore
from offline_speech import OfflineSpeech
from landing_status import FEEDBACK

BASE = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--listen', action='store_true', help='Microphone preview, no aircraft')
    mode.add_argument('--connect', action='store_true', help='Connect aircraft, then listen unarmed')
    mode.add_argument('--text', action='store_true', help='Keyboard-only preview')
    mode.add_argument('--check-speech', action='store_true', help='Check installed offline engine without microphone')
    mode.add_argument('--emulate', help='Test phrase against grammar without microphone or aircraft')
    mode.add_argument('--calibrate', metavar='DIRECTION', help='Record measured calibration; no aircraft connection')
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--video', action='store_true', help='Local live camera preview; requires --connect')
    measurement = parser.add_mutually_exclusive_group()
    measurement.add_argument('--meters', type=float)
    measurement.add_argument('--degrees', type=float)
    args = parser.parse_args()
    if args.video and not args.connect:
        parser.error('--video 需要同时使用 --connect')
    if not any((args.listen, args.connect, args.text, args.check_speech, args.emulate, args.calibrate)):
        parser.print_help()
        return
    store = CalibrationStore()
    if args.calibrate:
        direction = next((key for key, names in ALIASES.items()
                          if args.calibrate == key or args.calibrate in names), None)
        if direction is None or args.seconds is None:
            parser.error('标定需要有效方向和 --seconds')
        try:
            store.record_sample(direction, args.seconds, distance_m=args.meters, angle_degrees=args.degrees)
        except ValueError as exc:
            parser.error(str(exc))
        print('实测标定已保存：', store.path)
        return
    speech = controller = routing = executor = video = None
    event_queue = queue.Queue()
    flight_log = None
    try:
        if args.check_speech or args.emulate:
            speech = OfflineSpeech(check=args.check_speech, emulate=args.emulate)
            failed = False
            for event in speech.events():
                print(json.dumps(event, ensure_ascii=False))
                failed |= event.get('type') == 'error'
            if failed:
                raise SystemExit(1)
            return
        # Check microphone setup before creating an aircraft session.
        if not args.text:
            speech = OfflineSpeech()
            def read_speech():
                for event in speech.events():
                    event_queue.put(('speech', event))
                event_queue.put(('speech', {'type': 'error', 'message': '离线识别已停止'}))
            threading.Thread(target=read_speech, daemon=True).start()
            source, first = event_queue.get(timeout=30)
            if first.get('type') != 'ready':
                raise RuntimeError(str(first))
            source, listening = event_queue.get(timeout=30)
            if listening.get('type') != 'listening':
                raise RuntimeError(str(listening))
            print('离线中文识别引擎已加载：', first['recognizer'])
        if args.connect:
            from flight_controller import FlightController
            from network import detect_local_ip
            from registration_exchange import PROFILE
            from routing_sequences import RoutingSequenceStore, historical_next_counter
            if args.video:
                from video_stream import VideoReceiver
                video = VideoReceiver(preview=True)
                print('相机预览地址（浏览器打开）：', video.start())
            state_path = BASE / '.state' / 'routing-sequence.json'
            if not state_path.exists():
                raise RuntimeError('缺少接续路由状态；请先转移本设备最新 .state/routing-sequence.json，不从低序号重新开始')
            routing = RoutingSequenceStore(state_path)
            controller = FlightController.connect(detect_local_ip(), reliable=True, tick_interval=.05,
                                                   registration_profile=PROFILE, routing_sequences=routing,
                                                   video_receiver=video)
            if not controller.initialize_control_link():
                raise RuntimeError('飞机初始化未完成，未启用语音动作')
            print('飞机初始化成功。请在键盘输入“启用语音”后再说飞行口令。')
        else:
            print('预览模式：只识别和显示，不发送飞行动作。')
        executor = VoiceExecutor(controller, store)
        print('口令：起飞→确认（先目视确认已落地）；已在空中无需再起飞；向前/后/上/下飞一点；停止；降落。')
        print('八方向均支持指定秒数，最长10秒；例如“向前飞十秒”“左转五秒”。')
        print('环绕：说“半径五米”，再说“环绕→确认”；或“环绕五米→确认”。默认2米，支持2–10米。')
        print('“停止环绕”或“停止”取消任务；“降落”停止任务并请求降落。环绕可能自动起飞。')
        print('相机口令：拍照 | 开始录像 | 停止录像 | 相册 | 下载最新照片 | 下载最新录像。')
        print('原片保存到 captures/pc-downloads；录像需先停止后下载。')
        print('停止环绕后等飞机回报悬停，再说方向口令；落地后可直接“起飞→确认”或“环绕→确认”，无需重连。')
        print('键盘同样可输入：停止、降落、暂停语音、状态、退出。米数需先标定。')
        def keyboard():
            while True:
                try:
                    value = input()
                except (EOFError, KeyboardInterrupt):
                    value = '退出'
                event_queue.put(('keyboard', value))
                if value.strip() == '退出':
                    break
        threading.Thread(target=keyboard, daemon=True).start()
        last_feedback = None
        while True:
            executor.poll_camera()
            if controller:
                with controller._lock:
                    landing = controller.landing_status.snapshot()
                token = (landing['requested_at'], landing['phase'])
                if token != last_feedback and landing['requested_at'] is not None:
                    message = FEEDBACK.get(landing['phase'])
                    if message:
                        print(message)
                    last_feedback = token
            try:
                source, event = event_queue.get(timeout=.25)
            except queue.Empty:
                continue
            if source == 'keyboard':
                executor.handle(event, manual=True)
                if event.strip() == '退出':
                    break
            elif event.get('type') == 'recognized':
                print(f'识别：{event["text"]}（{event["confidence"]:.2f}）')
                # Spoken exit pauses/centers only; closing a flight session requires keyboard.
                text = '暂停语音' if event['text'] == '退出' else event['text']
                executor.handle(text, confidence=float(event['confidence']),
                                event_time=float(event['at_unix_ms']) / 1000)
            elif event.get('type') == 'error':
                executor.handle('暂停语音', manual=True)
                print('语音已暂停：', event.get('message'), '；可继续用键盘控制或退出。')
    except (OSError, RuntimeError, ValueError, queue.Empty) as exc:
        print('无法继续：', exc)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('正在回中并结束会话……')
    finally:
        for resource in (executor, speech):
            if resource:
                try:
                    resource.close()
                except Exception as exc:
                    print('关闭组件时发生错误：', exc)
        try:
            if controller:
                with controller._lock:
                    landing = controller.landing_status.snapshot()
                if landing.get('requested_at') is not None and landing['phase'] != 'motors_off':
                    print('关闭会话时仍未确认电机停止；请目视确认飞机，并用原厂控制方式接管落地。')
                controller.close()
                flight_log = str(controller.save_log())
                print('飞行记录：', flight_log)
        finally:
            if video:
                video.close()
            if routing:
                routing.close()
        if executor:
            captures = BASE / 'captures'
            captures.mkdir(exist_ok=True)
            output = captures / f'voice-session-{datetime.datetime.now():%Y%m%d-%H%M%S}.json'
            output.write_text(json.dumps({'flight_log': flight_log, 'events': list(executor.events)},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
            print('语音记录：', output)

if __name__ == '__main__':
    main()
