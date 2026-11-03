"""Interruptible single-action voice dispatcher, preview by default."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from collections import deque
import math
import threading
import time
from pathlib import Path
from voice_commands import parse_voice_command
from voice_motion import plan_motion

CAMERA_ACTIONS=('camera_photo','camera_record_start','camera_list','camera_download_photo','camera_download_video')
MEDIA_OUTPUT=Path(__file__).resolve().parents[1]/'captures'/'pc-downloads'

class VoiceExecutor:
    def __init__(self, controller, calibration, notify=print, circle=None, camera=None,dolly=None):
        self.controller, self.calibration, self._output = controller, calibration, notify
        if circle is None and controller is not None and hasattr(controller, 'request_native'):
            from native_circle import NativeCircle
            circle = NativeCircle(controller)
        self.circle = circle
        if camera is None and controller is not None and hasattr(controller,'request_camera'):
            from native_camera import NativeCamera
            camera=NativeCamera(controller)
        self.camera=camera
        if dolly is None and controller is not None and hasattr(controller,'request_dolly_zoom'):
            from native_dolly_zoom import NativeDollyZoom
            dolly=NativeDollyZoom(controller,camera)
        self.dolly=dolly
        self.dolly_config=dict(framing='半身',zoom=2,retreat=True)
        self._dolly_used=False
        self._dolly_feedback_last=None
        self._landing_prompted_for=None
        self._landing_auto_request=None
        self._camera_cancel=None
        self.circle_radius_m = 2.
        self._circle_used = False
        self.enabled = False
        self.events = deque(maxlen=2000)
        self._worker = None
        self._cancel = threading.Event()
        self._pending = None
        self._lock = threading.RLock()
        self.closed = False

    def notify(self, message):
        self.events.append({'at': time.time(), 'result': str(message)})
        self._output(message)

    def poll_camera(self):
        if self.camera is not None:
            message=self.camera.poll_recording_status()
            if isinstance(message,str) and message:self.notify(message)
        if self.dolly and self._dolly_used:
            observation=self.controller.status().get('subject_observation',{})
            task=observation.get('task')
            payload=None if task is None else task.get('payload_hex')
            if payload!=self._dolly_feedback_last:
                self._dolly_feedback_last=payload
                self.notify('滑动变焦任务状态：'+str(payload or '暂无新鲜数据')+
                            '；候选主体：'+str(observation.get('candidate_count')))
        if self.controller is not None and hasattr(self.controller,'confirm_landing'):
            from landing_confirmation import eligible_landing_confirmation
            status=self.controller.status()
            requested=status.get('landing_observation',{}).get('requested_at')
            with self._lock:
                if (requested is not None and requested==self._landing_auto_request
                        and requested!=self._landing_prompted_for
                        and eligible_landing_confirmation(status)):
                    self._landing_prompted_for=requested
                    self._landing_auto_request=None
                    try:
                        reply=self.controller.confirm_landing(confirmed=True,timeout=.4)
                        self.notify('已按手机流程发送后续降落指令，回复：'+
                                    (reply.hex() if reply is not None else '未收到')+'；仍需目视确认实际落地')
                    except (OSError,RuntimeError,ValueError,TimeoutError) as exc:
                        self.notify('后续降落指令未确认，未自动重试：'+str(exc))

    @property
    def busy(self):
        return self._worker is not None and self._worker.is_alive()

    def _stop(self):
        self._cancel.set()
        self._pending = None
        self._landing_auto_request=None
        if self.controller:
            try:
                self.controller.center_sticks()
            except (OSError, RuntimeError) as exc:
                self.notify('回中未确认：' + str(exc))
        if self.circle and self._circle_used:
            try:
                reply = self.circle.stop(timeout=.4)
                self.notify('停止环绕回复：' + (reply.hex() if reply is not None else '未收到') + '；请观察飞机是否悬停')
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.notify('停止环绕未确认：' + str(exc))
        if self.dolly and self._dolly_used:
            try:
                if self.dolly.started_at is not None and not self.dolly.is_active_or_unknown():
                    self.notify('滑动变焦已回报结束，不再追加停止任务指令')
                else:
                    reply=self.dolly.stop(timeout=.4)
                    self.notify('停止滑动变焦回复：'+(reply.hex() if reply is not None else '未收到')+'；请观察是否悬停')
            except (OSError,RuntimeError,TimeoutError) as exc:
                self.notify('停止滑动变焦未确认：'+str(exc))

    def _reset_circle_cycle(self):
        if self.circle:
            self.circle.land_requested = False
            self.circle.started_at = None
            self.circle.prepared = False
        self._circle_used = False
        if self.dolly:
            self.dolly.land_requested=False
            self.dolly.started_at=None
            self.dolly.prepared=False
        self._dolly_used=False
        self._dolly_feedback_last=None

    def handle(self, text, confidence=1., manual=False, event_time=None):
        with self._lock:
            try:
                if self.closed:
                    return
                intent = parse_voice_command(text)
                threshold = .5 if intent.kind in ('land', 'center', 'circle_stop','dolly_stop') else .7
                if not manual and (not math.isfinite(confidence) or confidence < threshold or
                                   event_time is not None and abs(time.time() - event_time) > 3):
                    self.events.append({'at': time.time(), 'text': text, 'confidence': confidence,
                                        'rejected': True, 'threshold': threshold, 'event_time': event_time})
                    self.notify(f'忽略低置信度或过期口令：{text}（阈值 {threshold:.2f}）')
                    return
                self.events.append({'at': time.time(), 'text': text, 'confidence': confidence,
                                    'manual': manual, 'kind': intent.kind})
                kind = intent.kind
                if kind == 'status':
                    self.notify(str(self.controller.status()) if self.controller else '离线预览模式')
                    self.notify(f'下次环绕半径：{self.circle_radius_m:g} 米')
                    self.notify('下次滑动变焦：'+str(self.dolly_config))
                    return
                if kind == 'enable':
                    if manual and self.controller:
                        self.enabled = True
                        self.notify('语音动作已启用')
                    else:
                        self.notify('需要在键盘输入“启用语音”；预览模式不发送动作')
                    return
                if kind in ('pause', 'center', 'circle_stop', 'dolly_stop','exit'):
                    if kind in ('pause', 'exit'):
                        self.enabled = False
                    if kind == 'circle_stop':
                        self._circle_used = True
                    if kind=='dolly_stop':
                        self._dolly_used=True
                    if self.controller:
                        self._stop()
                    self.notify('四轴回中' if self.controller else '预览：四轴回中')
                    return
                plan = plan_motion(intent, self.calibration) if kind == 'move' else None
                if not self.controller or not (self.enabled or manual and kind in ('land','camera_record_stop')):
                    self.notify(f'预览：{text}' + (f'；动作时间 {sum(plan.segments):g} 秒' if plan else '') + '；未发送')
                    return
                if kind=='camera_record_stop':
                    self._pending=None
                    if self._camera_cancel is not None:self._camera_cancel.set()
                    if self.camera is None:raise RuntimeError('当前控制器不支持原生相机')
                    reply=self.camera.stop_recording(timeout=.4)
                    self.notify(('停止录像回复：' if reply==b'\x00' else '停止录像未获成功回复：')+
                                (reply.hex() if reply is not None else '未收到')+
                                '；请用“相册”核对机内文件，再说“下载最新录像”保存到 PC')
                    return
                if kind == 'land':
                    if self.circle and self._circle_used:
                        self.circle.land_requested = True
                    if self.dolly and self._dolly_used:self.dolly.land_requested=True
                    self._stop()
                    self._landing_prompted_for=None
                    reply = self.controller.land()
                    if reply==b'\x00' and hasattr(self.controller,'confirm_landing'):
                        self._landing_auto_request=self.controller.status().get('landing_observation',{}).get('requested_at')
                    self.notify('降落指令回复：' + (reply.hex() if reply is not None else '未收到') +
                                '；尚未确认落地，请观察飞机，后续状态会另行提示。')
                    return
                if self.busy:
                    self.notify('当前动作未结束，未排队；可说“停止”或“降落”')
                    return
                status = self.controller.status()
                flight = status.get('landing_observation', {})
                fresh = flight.get('telemetry_age_s') is not None and 0 <= flight['telemetry_age_s'] <= 1
                grounded = fresh and flight.get('motors_on') is False and flight.get('airborne_flag') is False
                airborne = fresh and flight.get('motors_on') is True and flight.get('airborne_flag') is True
                if kind in ('takeoff', 'takeoff_again') and airborne:
                    self._pending = None
                    self.notify('飞机已在空中，无需再次起飞；停止原生任务后等待悬停状态，可继续方向操作')
                    return
                if (self.circle and self.circle.is_active_or_unknown()
                        and kind not in CAMERA_ACTIONS
                        and not (grounded and kind in ('takeoff', 'takeoff_again', 'circle', 'dolly','confirm'))):
                    self.notify('环绕任务尚未确认结束；先说“停止环绕”或“降落”')
                    return
                if (self.dolly and self.dolly.is_active_or_unknown()
                        and not (grounded and kind in ('takeoff','takeoff_again','circle','dolly','confirm'))):
                    self._pending=None
                    self.notify('滑动变焦任务尚未确认结束；先说“停止滑动变焦”或“降落”')
                    return
                if kind.startswith('dolly'):
                    if self.dolly is None:raise RuntimeError('当前控制器不支持原生滑动变焦')
                    self._pending=None
                    if kind in ('dolly_framing','dolly_zoom','dolly_retreat'):
                        if kind=='dolly_framing':self.dolly_config['framing']=intent.value
                        elif kind=='dolly_zoom':self.dolly_config['zoom']=int(intent.value)
                        else:self.dolly_config['retreat']=intent.value=='on'
                        self.dolly.prepared=False
                        self.notify('下次滑动变焦设置已更新，尚未发送：'+str(self.dolly_config))
                        return
                    if not grounded:
                        self.notify('滑动变焦目前仅从地面启动；请先降落并确认状态')
                        return
                    if kind=='dolly':
                        self._pending=('dolly',time.monotonic()+10,self.dolly_config.copy())
                        self.notify('将启动滑动变焦，可能自动起飞；请目视确认已落地，10秒内说“确认”')
                        return
                if kind in ('circle', 'circle_prepare', 'circle_radius'):
                    if not self.circle:
                        raise RuntimeError('当前控制器不支持原生环绕')
                    self._pending = None
                    if intent.amount:
                        self.circle_radius_m = intent.amount
                    if kind == 'circle_radius':
                        self.notify(f'下次环绕半径设为 {self.circle_radius_m:g} 米；启动前写入并读回核对')
                        return
                    if kind == 'circle':
                        self._pending = ('circle', time.monotonic() + 10, self.circle_radius_m)
                        self.notify(f'将环绕 {self.circle_radius_m:g} 米，可能自动起飞；确认已落地或保持安全悬停，10 秒内说“确认”')
                        return
                if kind in ('takeoff', 'takeoff_again') and not manual:
                    self._pending = (kind, time.monotonic() + 10)
                    self.notify('请目视确认已落地，10 秒内说“确认”')
                    return
                if kind in ('confirm', 'confirm_takeoff', 'confirm_again'):
                    pending, self._pending = self._pending, None
                    expected = (pending[0] if kind == 'confirm' and pending else
                                'takeoff' if kind == 'confirm_takeoff' else 'takeoff_again')
                    if pending is None or pending[0] != expected or time.monotonic() > pending[1]:
                        self.notify('没有有效的待确认口令，或确认已超时；请重新说出动作')
                        return
                    kind = expected
                    if kind == 'circle':
                        self.circle_radius_m = pending[2]
                    if kind=='dolly':
                        self.dolly_config=pending[2].copy()
                else:
                    self._pending = None
                cancel = self._cancel = threading.Event()
                if kind in CAMERA_ACTIONS:
                    self._camera_cancel=cancel
                radius = self.circle_radius_m
                dolly_config=self.dolly_config.copy()
                if kind in ('circle', 'circle_prepare'):
                    self._circle_used = True
                if kind in ('dolly','dolly_prepare'):self._dolly_used=True
                def run():
                    try:
                        if cancel.is_set():
                            return
                        if kind in ('dolly','dolly_prepare'):
                            current=self.controller.status()
                            prior_cycle=any(current.get(key) for key in
                                            ('takeoff_sent','takeoff_acknowledged','native_task_may_fly','land_requested','land_sent'))
                            if prior_cycle:
                                if kind!='dolly':raise RuntimeError('新一轮任务请说“滑动变焦→确认”，先目视确认已落地')
                                self.controller.prepare_grounded_cycle(confirmed_grounded=True)
                                self._reset_circle_cycle()
                                self._dolly_used=True
                            self.dolly.prepare(**dolly_config,cancel_event=cancel)
                            self.notify('滑动变焦准备完成：'+str(dolly_config))
                            if kind=='dolly' and not cancel.is_set():
                                reply=self.dolly.start(confirmed=True,cancel_event=cancel)
                                self.notify('原生滑动变焦启动回复：'+(reply.hex() if reply is not None else '未收到，任务仍可能已启动')+
                                            '；可说“停止滑动变焦”或“降落”，请观察实际动作')
                        elif kind in CAMERA_ACTIONS:
                            if self.camera is None:raise RuntimeError('当前控制器不支持原生相机')
                            if kind=='camera_list':
                                entries=self.camera.list_media(cancel_event=cancel)
                                self.notify(f'飞机相册：{len(entries)} 项；最新项目：\n'+'\n'.join(
                                    f'{entry["name"]}（{entry.get("size") or "大小待解析"} 字节）' for entry in entries[:10]))
                            elif kind in ('camera_download_photo','camera_download_video'):
                                self.notify('正在读取飞机相册并下载最新原片……；可说“停止”取消')
                                kind_name='photo' if kind=='camera_download_photo' else 'video'
                                result=self.camera.download_latest(kind_name,MEDIA_OUTPUT,cancel_event=cancel)
                                self.notify('原片已校验并保存：'+result['path'])
                            else:
                                reply=(self.camera.shoot(cancel_event=cancel) if kind=='camera_photo' else
                                       self.camera.start_recording(cancel_event=cancel))
                                self.notify(('拍照请求回复：' if kind=='camera_photo' else '开始录像请求回复：')+
                                            (reply.hex() if reply is not None else '未收到')+
                                            ('；请核对相机状态与保存文件' if kind=='camera_photo' else '；正在核对是否持续录像，00 仅为请求回复'))
                        elif kind in ('circle', 'circle_prepare'):
                            current = self.controller.status()
                            prior_cycle = any(current.get(key) for key in ('native_task_may_fly', 'land_requested', 'land_sent'))
                            flight_now = current.get('landing_observation', {})
                            if (prior_cycle and flight_now.get('motors_on') is False
                                    and flight_now.get('airborne_flag') is False):
                                if kind != 'circle':
                                    raise RuntimeError('新一轮环绕需要确认已落地；请说“环绕→确认”')
                                self.controller.prepare_grounded_cycle(confirmed_grounded=True)
                                self._reset_circle_cycle()
                                self._circle_used = True
                            self.circle.prepare(radius, cancel_event=cancel)
                            self.notify(f'环绕半径已读回：{radius:g} 米')
                            if kind == 'circle' and not cancel.is_set():
                                reply = self.circle.start(confirmed=True, cancel_event=cancel)
                                self.notify('原生环绕启动回复：' + (reply.hex() if reply is not None else '未收到，任务仍可能启动') +
                                            '；可说“停止环绕”或“降落”')
                                if hasattr(self.circle, 'wait_execution'):
                                    outcome = self.circle.wait_execution(cancel_event=cancel)
                                    if outcome.get('executing'):
                                        self.notify('已观察到环绕执行状态；请核对实际运动')
                                    elif not outcome.get('canceled'):
                                        self.notify(f'未观察到环绕执行；任务状态：{outcome.get("task")}，候选主体：{outcome.get("candidate_count")}；'
                                                    '00 仅是请求回复，请将相机对准主体；可停止或降落')
                        elif plan:
                            self.notify(('按标定速度估算；' if plan.estimated else '') + f'开始：{text}')
                            for duration in plan.segments:
                                with self._lock:
                                    if cancel.is_set():
                                        break
                                self.controller.move(plan.direction, duration, cancel_event=cancel)
                                if cancel.wait(.15):
                                    break
                            self.notify('动作结束，四轴回中')
                        else:
                            with self._lock:
                                if cancel.is_set():
                                    return
                            current = self.controller.status()
                            restart = kind == 'takeoff_again' or any(current.get(key) for key in
                                ('takeoff_sent', 'takeoff_acknowledged', 'native_task_may_fly', 'land_sent', 'land_requested'))
                            reply = (self.controller.takeoff_again(confirmed_grounded=True, cancel_event=cancel) if restart
                                     else self.controller.takeoff(cancel_event=cancel))
                            if reply == b'\x00':
                                self._reset_circle_cycle()
                            self.notify('起飞回复：' + (reply.hex() if reply is not None else '未收到，请目视确认'))
                    except Exception as exc:
                        self.notify('动作未完成：' + str(exc))
                self._worker = threading.Thread(target=run, daemon=True)
                self._worker.start()
            except (ValueError, OSError, RuntimeError) as exc:
                self.notify(str(exc))

    def close(self):
        self.enabled = False
        self.closed = True
        try:
            self._stop()
        except (OSError, RuntimeError) as exc:
            self.notify('结束时回中未确认：' + str(exc))
        if self._worker:
            self._worker.join(timeout=3)
        if self.camera:
            try:
                self.camera.close()
            except (OSError,RuntimeError) as exc:
                self.notify('退出时停止录像未确认：'+str(exc))
