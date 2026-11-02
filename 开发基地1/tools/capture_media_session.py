"""Bounded iPhone capture for camera controls AND file transfers; no drone sends."""
import argparse
import asyncio
import datetime
import json
import math
from pathlib import Path
import time

BASE = Path(__file__).resolve().parents[1]


def validate_limits(seconds, max_bytes):
    if not math.isfinite(seconds) or not 0 < seconds <= 600 or not 0 < max_bytes <= 1024**3:
        raise ValueError('采集最多 600 秒、1 GiB，时长和大小必须大于零')


async def capture_session(output, seconds=240, max_bytes=512*1024**2, *, connect=None, service_class=None):
    validate_limits(seconds, max_bytes)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('不会覆盖已有抓包：' + str(output))
    if connect is None:
        from pymobiledevice3.lockdown import create_using_usbmux
        connect = create_using_usbmux
    if service_class is None:
        from pymobiledevice3.services.pcapd import PcapdService
        service_class = PcapdService
    stop_path = output.with_suffix('.stop')
    report_path = output.with_suffix('.capture.json')
    report = dict(output=str(output), stop_file=str(stop_path), packets=0, started_at=None,
                  ended_at=None, reason=None, seconds_limit=seconds, bytes_limit=max_bytes,
                  scope='iPhone network; all ports, includes TCP media transfers')
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    link = await asyncio.wait_for(connect(connection_type='USB', autopair=False), timeout=20)
    try:
        async with service_class(link) as service:
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also protects against a race after exists().
            with output.open('xb') as out:
                report['started_at'] = time.time()
                save()
                print('抓包已开始：', output, flush=True)
                print('采集所有端口；仅操作 DJI Fly。完成后可 Ctrl+C，或创建停止标记：', stop_path, flush=True)
                async def packets():
                    async for packet in service.watch():
                        report['packets'] += 1
                        yield packet
                        if out.tell() >= max_bytes:
                            report['reason'] = 'size_limit'
                            return
                async def stopped():
                    while not stop_path.exists():
                        await asyncio.sleep(.1)
                    return 'stop_file'
                writer = asyncio.create_task(service.write_to_pcap(out, packets()))
                stopper = asyncio.create_task(stopped())
                try:
                    done, _ = await asyncio.wait((writer, stopper), timeout=seconds,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if writer in done:
                        await writer
                        report['reason'] = report['reason'] or 'stream_ended'
                    elif stopper in done:
                        report['reason'] = 'stop_file'
                    else:
                        report['reason'] = 'timeout'
                except BaseException as exc:
                    report['reason'] = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'error'
                    report['error'] = type(exc).__name__ + ': ' + str(exc)
                    raise
                finally:
                    writer.cancel()
                    stopper.cancel()
                    await asyncio.gather(writer, stopper, return_exceptions=True)
                    out.flush()
                    report['ended_at'] = time.time()
                    report['saved_bytes'] = out.tell()
                    save()
    finally:
        await link.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--capture', action='store_true', help='Capture USB-connected iPhone traffic')
    mode.add_argument('--stop', type=Path, help='Existing .capture.json session to stop')
    parser.add_argument('--seconds', type=float, default=240)
    parser.add_argument('--max-mib', type=int, default=512)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.stop:
        report = json.loads(args.stop.read_text(encoding='utf-8'))
        output = Path(report['output']).resolve()
        expected = output.with_suffix('.stop')
        if Path(report['stop_file']).resolve() != expected or args.stop.resolve() != output.with_suffix('.capture.json'):
            parser.error('停止标记与会话路径不匹配')
        expected.write_text('stop', encoding='utf-8')
        print('已请求停止采集。')
        return
    if not args.capture:
        parser.print_help()
        return
    try:
        validate_limits(args.seconds, args.max_mib * 1024**2)
        stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
        output = args.output or BASE/'captures'/f'camera-media-{stamp}.pcapng'
        report = asyncio.run(capture_session(output, args.seconds, args.max_mib * 1024**2))
        print(json.dumps(report, ensure_ascii=False))
    except KeyboardInterrupt:
        print('采集已停止，已保存已接收的数据；这不会停止手机上的录像，请在 DJI Fly 中确认。')
    except Exception as exc:
        parser.exit(1, f'无法采集：{type(exc).__name__}: {exc}\n请连接并解锁已信任的 iPhone。\n')


if __name__ == '__main__':
    main()
