"""Optional original keyboard flight console."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from flight_controller import *
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', action='store_true',
                        help='Connect to the aircraft and open the manual control prompt')
    parser.add_argument('--legacy-transport', action='store_true',
                        help='Use the previous unsequenced type-4 transport for comparison')
    parser.add_argument('--auto-land-after', type=float, default=None, metavar='SECONDS',
                        help='Opt in to timed landing after takeoff; disabled by default')
    args = parser.parse_args()
    if not args.connect:
        print('预览模式：加入 --connect 后才连接飞机；飞行动作需要在提示符中输入。')
        print(COMMAND_HELP)
        return
    routing = None
    try:
        local_ip = detect_local_ip()
        if not args.legacy_transport:
            if not STATE_PATH.exists():
                print('首次启动正在整理历史会话序号……')
            routing = RoutingSequenceStore(initial=lambda: historical_next_counter(
                Path(__file__).resolve().parents[1] / 'captures'))
        controller = FlightController.connect(local_ip, reliable=not args.legacy_transport,
                                               tick_interval=0.05,
                                               registration_profile=None if args.legacy_transport else PROFILE,
                                               routing_sequences=routing,
                                               max_flight_seconds=args.auto_land_after)
    except (OSError, ValueError, TimeoutError, RuntimeError) as exc:
        if routing is not None:
            routing.close()
        print('无法打开控制会话：', exc)
        raise SystemExit(1)
    except BaseException:
        if routing is not None:
            routing.close()
        raise
    print('已收到握手回复；电脑飞控是否被飞机接受仍待本次验证。')
    try:
        if controller.transport is not None:
            print('正在自动建立可靠传输并核对飞控设备回复……')
            ready = False
            try:
                ready = controller.initialize_control_link()
                reply = controller.device_reply
                if reply is not None:
                    model = reply[1:].split(b'\x00', 1)[0].decode('ascii', errors='replace')
                    print('飞控设备回复：', model or '已收到')
                else:
                    print('飞控设备回复：未收到')
            except (OSError, RuntimeError) as exc:
                print('初始化未完成：', exc)
            print('传输确认：', controller.transport.status())
            print('应用注册：', controller.registration.status())
            if not ready:
                print('初始化未完成，控制台已结束；请查看保存的会话记录。')
                return
            print('初始化完成：飞控设备回复和新鲜视频均已确认。')
        print('定时降落：关闭' if controller.max_flight_seconds is None else
              f'定时降落：每次起飞后 {controller.max_flight_seconds:g} 秒')
        print(COMMAND_HELP)
        while True:
            try:
                command = input('neo2> ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if command in ('quit', '退出'):
                break
            if command in ('status', '状态'):
                print(controller.status())
            elif command in ('neutral', '回中'):
                try:
                    controller.center_sticks()
                    print('四轴已回中')
                except (OSError, RuntimeError) as exc:
                    print('回中发送失败：', exc)
            elif command in ('startup', '基础查询'):
                try:
                    reply = controller.probe_startup_queries()
                    print('基础查询：', '收到飞控同序号回复' if reply is not None else
                          '未收到飞控同序号回复')
                    print('当前状态：', controller.status())
                except (OSError, RuntimeError) as exc:
                    print('基础查询失败：', exc)
            elif command in ('query', '查询设备'):
                try:
                    reply = controller.query_device()
                    if reply is None:
                        print('设备查询未收到飞控回复')
                    else:
                        model = reply[1:].split(b'\x00', 1)[0].decode('ascii', errors='replace')
                        print('设备查询飞控回复：', model or reply.hex())
                except (OSError, RuntimeError) as exc:
                    print('设备查询失败：', exc)
            elif command in ('takeoff', '起飞'):
                try:
                    reply = controller.takeoff()
                    print('起飞飞控回复：', reply.hex() if reply is not None else '未收到；请观察飞机，若实际起飞可输入“降落”')
                except (OSError, RuntimeError) as exc:
                    print('起飞未完成：', exc)
            elif command in ('takeoff_again', '再次起飞'):
                try:
                    reply = controller.takeoff_again(confirmed_grounded=True)
                    print('再次起飞飞控回复：', reply.hex() if reply is not None else
                          '未收到；请目视确认飞机状态')
                except (OSError, ValueError, RuntimeError) as exc:
                    print('再次起飞未完成：', exc)
            elif command in ('land', '降落'):
                try:
                    reply = controller.land()
                    print('降落指令回复：', reply.hex() if reply is not None else '未收到',
                          '；这不代表已落地，请观察飞机或输入“状态”查看降落监测。')
                except (OSError, RuntimeError) as exc:
                    print('降落发送失败：', exc)
            else:
                try:
                    movement = parse_movement_command(command)
                    if movement is None:
                        print('未识别的命令。' + COMMAND_HELP)
                        continue
                    direction, seconds = movement
                    controller.move(direction, seconds)
                    print(f'{MOVEMENTS[direction][0]} {seconds:g}秒结束，四轴已回中')
                except (OSError, ValueError, RuntimeError) as exc:
                    print(exc)
    finally:
        try:
            controller.close()
            print('会话记录已保存：', controller.save_log())
        finally:
            if routing is not None:
                routing.close()

if __name__=='__main__':main()
