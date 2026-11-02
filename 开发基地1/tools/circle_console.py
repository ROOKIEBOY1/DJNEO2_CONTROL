"""Explicit manual trial of native 2 m Circle. Default prints help, no connection."""
import argparse
import json
from pathlib import Path
import queue
import threading
import time
from native_circle import NativeCircle

HELP = '命令：准备 | 环绕 | 确认 | 停止 | 降落 | 状态 | 退出'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', action='store_true')
    args = parser.parse_args()
    if not args.connect:
        print('预览：原生环绕固定 2 米，加入 --connect 后连接飞机。')
        print('启动原生任务可能从地面自动起飞并识别人物；不需要先输入“起飞”。')
        print('操作：准备 → 环绕 → 确认；中途可停止、降落。退出会尝试停止任务并降落。')
        print('电脑端 2 米环绕已由操作者实飞确认。语音及可调半径请用 voice_console.py。'+HELP)
        return
    from flight_console import FlightController, detect_local_ip, PROFILE
    from routing_sequences import RoutingSequenceStore, historical_next_counter
    base = Path(__file__).resolve().parents[1]
    routing = controller = mission = worker = None
    job_cancel = threading.Event()
    events = queue.Queue()
    try:
        routing = RoutingSequenceStore(initial=lambda: historical_next_counter(base/'captures'))
        controller = FlightController.connect(detect_local_ip(), reliable=True, tick_interval=.05,
            registration_profile=PROFILE, routing_sequences=routing)
        if not controller.initialize_control_link():
            raise RuntimeError('设备或新鲜视频未确认，未开放环绕控制')
        mission = NativeCircle(controller)
        print('飞机初始化完成。先输入“准备”，仅设置模式、写入并读回 2 米参数。')
        print('“环绕→确认”可能自动起飞；请让相机能看到人物。主体框不等于已验证锁定。')
        print('已有语音/手动控制台需先在落地后退出；本入口使用相同会话身份。')
        print(HELP)
        def keyboard():
            while True:
                try:
                    text = input().strip()
                except (EOFError, KeyboardInterrupt):
                    text = '退出'
                events.put(text)
                if text == '退出':
                    break
        threading.Thread(target=keyboard, daemon=True).start()
        def run_job(action):
            nonlocal worker, job_cancel
            job_cancel = threading.Event()
            token = job_cancel
            def execute():
                try:
                    if action == 'prepare':
                        mission.prepare(cancel_event=token)
                        print('准备完成：模式回显和 2 米半径读回已核对。可输入“环绕”。')
                    else:
                        reply = mission.start(confirmed=True, cancel_event=token)
                        if token.is_set():
                            print('启动等待已结束；本地操作已取消，请核对停止状态和飞机实际表现。')
                        else:
                            print('原生环绕启动回复：', reply.hex() if reply is not None else '未收到，任务仍可能已启动',
                                  '；请观察飞机及下方状态，可随时输入“停止”或“降落”。')
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    print('操作未完成：', exc)
            worker = threading.Thread(target=execute, daemon=True)
            worker.start()
        pending_until = None
        last_state = None
        while True:
            observation = controller.status()['subject_observation']
            task = observation['task']
            state = None if task is None else task['payload_hex']
            if state != last_state:
                print('任务状态：', state or '暂缺新鲜数据', '；候选主体框：', observation['candidate_count'])
                last_state = state
            try:
                text = events.get(timeout=.25)
            except queue.Empty:
                continue
            if text == '退出':
                break
            if text == '状态':
                print(json.dumps(mission.status(), ensure_ascii=False, indent=2))
                continue
            if text in ('停止', '降落'):
                pending_until = None
                job_cancel.set()
                try:
                    reply = mission.stop(timeout=.4) if text == '停止' else mission.land()
                    print(text+'指令回复：', reply.hex() if reply is not None else '未收到',
                          '；回复不等于动作完成，请观察飞机。')
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    print(text+'未确认：', exc)
                continue
            if worker is not None and worker.is_alive():
                print('正在处理上一操作，未排队；可输入停止或降落。')
                continue
            if text == '准备':
                pending_until = None
                run_job('prepare')
            elif text in ('环绕', '环绕2', '环绕2米', '环绕 2', '环绕 2米'):
                if not mission.prepared:
                    print('请先输入“准备”，核对 2 米半径。')
                    continue
                pending_until = time.monotonic()+5
                print('将启动 2 米原生环绕，可能自动起飞。5 秒内输入“确认”；停止可取消。')
            elif text == '确认':
                pending, pending_until = pending_until, None
                if pending is None or time.monotonic() > pending:
                    print('没有有效的待确认环绕请求。')
                else:
                    run_job('start')
            else:
                print(HELP)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print('无法继续：', exc)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('正在结束任务会话……')
    finally:
        job_cancel.set()
        try:
            if mission is not None and controller.native_task_may_fly:
                try:
                    mission.land(timeout=.5)
                    print('退出时已尝试停止任务并降落，请目视确认实际落地。')
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    print('退出时停止/降落未确认：', exc)
            if worker is not None:
                worker.join(timeout=3)
        finally:
            try:
                if controller is not None:
                    controller.close()
                    print('会话记录：', controller.save_log())
            finally:
                if routing is not None:
                    routing.close()

if __name__ == '__main__':
    main()
