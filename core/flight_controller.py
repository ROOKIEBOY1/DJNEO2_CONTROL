"""Neo 2 control session with camera and media hooks; no CLI or UI."""

import datetime
import json
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path

from outer import parse_outer
from control_packets import (build_fly_packet, build_query_packet,
                             build_startup_query_packet,
                             find_command_reply)
from handshake import (DRONE_IP, DRONE_PORT, SHORT_REPLY_BODY,
                             build_handshake, new_video_seed, validate_retry_bounds)
from initial_010a import build_initial_010a_packet
from neutral_stick import build_stick_packet
from reliable_transport import ReliableTransport
from registration_exchange import PROFILE, RegistrationExchange, encode_message
from circle_protocol import (CIRCLE_SELECTION_PAYLOAD, CIRCLE_START_PAYLOAD, RADIUS_HASH_BYTES,
                             parse_radius_value, validate_radius)
from messages import duml_messages
from movement_commands import MOVEMENTS, validate_duration
from routing_sequences import resequence_tunnels
from landing_status import LandingStatus
from subject_observation import SubjectObservation
from native_camera import CameraObservation, validate_camera_request, PHOTO_CONTEXT_PAYLOAD


class _AnyCancellation:
    def __init__(self, *events):
        self.events = tuple(event for event in events if event is not None)

    def is_set(self):
        return any(event.is_set() for event in self.events)


class FlightController:
    def __init__(self, sock, remote_ip, remote_port, session, video_seed,
                 inner_sequence, outer_sequence, counter, tick_interval,
                 max_flight_seconds, reliable=False, registration_profile=None,
                 routing_sequences=None, video_receiver=None):
        self.sock = sock
        self.video_receiver = video_receiver
        self.video_receiver_error = None
        self.udp_receive_buffer_bytes = None
        self.remote = (remote_ip, remote_port)
        self.session = session
        self.landing_status = LandingStatus()
        self.subject_observation = SubjectObservation()
        self.camera_observation = CameraObservation()
        self._media_directory_lock=threading.Lock()
        self._media_directory_receiver=None
        self._media_directory_session=secrets.randbelow(65535)
        self.media_directory_entries=[]
        self.video_seed = video_seed
        self.video_word = video_seed
        self.video_ack = video_seed
        self.inner_sequence = inner_sequence
        self.outer_sequence = outer_sequence
        self.counter = counter
        self.tick_interval = tick_interval
        self.max_flight_seconds = max_flight_seconds
        self.transport = ReliableTransport(session, video_seed) if reliable else None
        if routing_sequences is not None and not reliable:
            raise ValueError('Persistent routing requires reliable transport')
        self.routing_sequences = routing_sequences
        if registration_profile is not None and not reliable:
            raise ValueError('Application registration requires reliable transport')
        self.registration = (RegistrationExchange(registration_profile, time.monotonic())
                             if registration_profile is not None else None)
        self.device_verified = False
        self.device_reply = None
        self._tx_lock = threading.Lock()
        self.outgoing = deque(maxlen=3000)
        self.mode = 'short'
        self.roll = 1024
        self.pitch = 1024
        self.throttle = 1024
        self.yaw = 1024
        self._stick_tx_lock = threading.RLock()
        self._movement_lock = threading.Lock()
        self._movement_cancel = threading.Event()
        self._flight_cancel = threading.Event()
        self.movements = []
        self.takeoff_acknowledged = False
        self.takeoff_sent = False
        self.takeoff_started_at = None
        self.native_task_may_fly = False
        self.native_started_at = None
        self.native_stop_confirmed_at = None
        self.flight_cycle = 0
        self.land_sent = False
        self.land_requested = False
        self.land_acknowledged = False
        self.auto_land_sent = False
        self._watchdog = None
        self.last_error = None
        self.received = 0
        self.last_receive_at = time.monotonic()
        self.last_video_at = None
        self.events = deque(maxlen=2000)
        self.control_events = deque(maxlen=10000)
        self.control_received = 0
        self.media_data_events=deque(maxlen=2048)
        self.media_data_received=0
        self.commands = []
        self._lock = threading.Lock()
        self._pending = {}
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)

    @classmethod
    def connect(cls, local_ip, remote_ip=DRONE_IP, remote_port=DRONE_PORT,
                session=None, video_seed=None, attempts=60, retry_interval=0.55,
                prepare_seconds=2.0, tick_interval=0.06,
                inner_sequence=None, outer_sequence=None, counter=None,
                max_flight_seconds=None, reliable=False, registration_profile=None,
                routing_sequences=None, video_receiver=None):
        validate_retry_bounds(attempts, retry_interval)
        if (not 0 <= prepare_seconds <= 3 or not 0.02 <= tick_interval <= 0.2 or
                max_flight_seconds is not None and not 0 < max_flight_seconds <= 120):
            raise ValueError('Preparation or tick interval outside bounds')
        session = session if session is not None else secrets.randbelow(0xffff) + 1
        video_seed = video_seed if video_seed is not None else new_video_seed()
        inner_sequence = inner_sequence if inner_sequence is not None else secrets.randbelow(0x10000)
        outer_sequence = outer_sequence if outer_sequence is not None else secrets.randbelow(0x10000)
        counter = counter if counter is not None else secrets.randbelow(0x1000000)
        request = build_handshake(session, video_seed)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        controller = None
        try:
            receive_buffer = None
            if video_receiver is not None:
                # Video arrives in bursts; allow room while Python handles
                # control replies. This reduces local loss, not radio loss.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                receive_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            sock.bind((local_ip, 0))
            matched = False
            for _ in range(attempts):
                sock.sendto(request, (remote_ip, remote_port))
                deadline = time.monotonic() + retry_interval
                while time.monotonic() < deadline:
                    sock.settimeout(max(0.001, deadline - time.monotonic()))
                    try:
                        raw, peer = sock.recvfrom(65535)
                    except socket.timeout:
                        break
                    if peer != (remote_ip, remote_port):
                        continue
                    try:
                        packet = parse_outer(raw)
                    except ValueError:
                        continue
                    if (packet.session == session and packet.channel == 0 and
                            packet.header_length == 8 and packet.body == SHORT_REPLY_BODY):
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                raise TimeoutError('No matching 15-byte drone handshake reply')
            controller = cls(sock, remote_ip, remote_port, session, video_seed,
                             inner_sequence, outer_sequence, counter, tick_interval,
                             max_flight_seconds, reliable=reliable,
                             registration_profile=registration_profile,
                             routing_sequences=routing_sequences, video_receiver=video_receiver)
            controller.udp_receive_buffer_bytes = receive_buffer
            sock.settimeout(0.05)
            controller._reader.start()
            controller._ticker.start()
            if prepare_seconds:
                time.sleep(prepare_seconds)
            if controller._stop.is_set():
                output = controller.save_log()
                raise RuntimeError('Connection stopped during preparation: ' +
                                   str(controller.last_error) + '; log: ' + str(output))
            with controller._lock:
                controller.mode = 'neutral'
            return controller
        except BaseException:
            if controller is not None:
                controller._stop.set()
                if controller._reader.is_alive():
                    controller._reader.join(timeout=1)
                if controller._ticker.is_alive():
                    controller._ticker.join(timeout=1)
            sock.close()
            raise

    def _next_inner(self):
        with self._lock:
            value = self.inner_sequence
            self.inner_sequence = (value + 1) & 0xffff
            return value

    def _reserve_inner(self, count):
        with self._lock:
            value = self.inner_sequence
            self.inner_sequence = (value + count) & 0xffff
            return value

    def _next_outer(self):
        with self._lock:
            value = self.outer_sequence
            self.outer_sequence = (value + 1) & 0xffff
            return value

    def _send(self, packet, *, duml=False, cancel_event=None):
        if self._stop.is_set():
            raise RuntimeError('Controller is closed')
        with self._tx_lock:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('动作已取消，命令未发送')
            if self._stop.is_set():
                raise RuntimeError('Controller is closed')
            if duml:
                if self.transport is None:
                    raise RuntimeError('Raw DUML requires reliable transport')
                payload = packet
            elif self.transport is not None and packet[6] == 4 and len(packet) > 34:
                payload = packet[34:]
            else:
                payload = None
            if payload is not None:
                if self.routing_sequences is not None:
                    payload = resequence_tunnels(payload, self.routing_sequences.next)
                packet = self.transport.encode(payload, time.monotonic())
            self.sock.sendto(packet, self.remote)
            if self.transport is not None:
                with self._lock:
                    self.outgoing.append({'time': time.time(), 'hex': packet.hex(),
                                          'retry': False})
            return packet

    def _service_transport(self):
        if self.transport is None:
            return
        with self._tx_lock:
            self.sock.sendto(self.transport.acknowledgement(), self.remote)
            for packet in self.transport.retries(time.monotonic()):
                self.sock.sendto(packet, self.remote)
                with self._lock:
                    self.outgoing.append({'time': time.time(), 'hex': packet.hex(),
                                          'retry': True})

    def _send_stick(self, force_neutral=False):
        with self._stick_tx_lock:
            sequence = self._next_inner()
            with self._lock:
                if (force_neutral or self.land_requested or
                        time.monotonic() - self.last_receive_at > 1):
                    self.roll = self.pitch = self.throttle = self.yaw = 1024
                axes = dict(roll=self.roll, pitch=self.pitch,
                            throttle=self.throttle, yaw=self.yaw)
                mode = 'neutral' if force_neutral else self.mode
                counter = self.counter
                self.counter = (counter + 50) & 0xffffff
            if mode == 'short':
                packet = build_initial_010a_packet(
                    self.session, self.video_seed, self.video_ack, sequence)
            else:
                packet = build_stick_packet(
                    self.session, self.video_seed, self.video_ack,
                    sequence, counter, video_word=self.video_word, **axes)
            return self._send(packet)

    def center_sticks(self, send=True):
        self._movement_cancel.set()
        with self._stick_tx_lock:
            with self._lock:
                self.roll = self.pitch = self.throttle = self.yaw = 1024
            if send and not self._stop.is_set():
                self._send_stick(force_neutral=True)

    def _tick_loop(self):
        while not self._stop.is_set():
            try:
                if self.registration is not None:
                    for frame in self.registration.poll(time.monotonic(), self._next_inner,
                                                         self._next_outer):
                        self._send(frame, duml=True)
                self._send_stick()
                self._service_transport()
            except (OSError, RuntimeError, ValueError) as exc:
                self.last_error = str(exc)
                self._stop.set()
                break
            self._stop.wait(self.tick_interval)

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                raw, peer = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self.last_error = str(exc)
                break
            if peer != self.remote:
                continue
            try:
                outer = parse_outer(raw)
            except ValueError:
                continue
            if outer.session != self.session:
                continue
            if self.transport is not None:
                with self._tx_lock:
                    self.transport.observe(raw)
            if self.registration is not None:
                for frame, _ in duml_messages(raw):
                    self.registration.observe(frame)
            self.received += 1
            self.last_receive_at = time.monotonic()
            if outer.channel==3:
                with self._lock:
                    directory=self._media_directory_receiver
                if directory is not None:
                    directory.feed(raw)
            # Submission only queues raw bytes. Assembly/decoding never runs on
            # this control reader or while holding the flight-state lock.
            if outer.channel == 2 and self.video_receiver is not None:
                try:
                    self.video_receiver.submit(raw)
                except Exception as exc:
                    self.video_receiver_error = str(exc)
            with self._lock:
                if outer.channel == 2:
                    advance = (outer.sequence - self.video_ack) & 0xffff
                    if 0 < advance < 0x8000:
                        self.video_ack = outer.sequence
                        self.last_video_at = time.monotonic()
                if self.transport is not None:
                    self.video_ack = self.transport.video_cursor
                    self.video_word = self.transport.downlink_cursor
                event = {'time': time.time(), 'channel': outer.channel,
                         'hex': raw.hex()}
                self.events.append(event)
                if outer.channel==3:
                    self.media_data_received+=1
                    self.media_data_events.append(event)
                if outer.channel == 1:
                    self.control_received += 1
                    self.control_events.append(event)
                    for frame, _ in duml_messages(raw):
                        self.landing_status.observe(frame, event['time'])
                        self.subject_observation.observe(frame, event['time'])
                        self.camera_observation.observe(frame, event['time'])
                pending = list(self._pending.items())
            for sequence, slot in pending:
                payload = find_command_reply(raw, self.session, sequence,
                                             slot['command_set'], slot['command_id'],
                                             sender=slot.get('sender', 3))
                if payload is not None:
                    slot['payload'] = payload
                    slot['event'].set()

    def _flight_command(self, param, timeout, *, cancel_event=None):
        if self._stop.is_set():
            raise RuntimeError('Controller stopped: ' + str(self.last_error))
        if param == 1:
            cancel_event = _AnyCancellation(self._flight_cancel, cancel_event)
        inner = self._next_inner()
        outer = self._next_outer()
        packet = build_fly_packet(self.session, self.video_seed, self.video_ack,
                                  self.video_word, outer, inner, param)
        slot = {'event': threading.Event(), 'payload': None,
                'command_set': 3, 'command_id': 0x2a}
        with self._lock:
            self._pending[inner] = slot
        try:
            wire_packet = (self._send(packet) if cancel_event is None else
                           self._send(packet, cancel_event=cancel_event))
            if param == 2:
                self.land_sent = True
            elif param == 1 and self.max_flight_seconds is not None:
                cycle = self.flight_cycle
                self._watchdog = threading.Timer(self.max_flight_seconds,
                                                  lambda: self._auto_land(cycle))
                self._watchdog.daemon = True
                self._watchdog.start()
            with self._lock:
                command_record = {'time': time.time(), 'param': param,
                                      'flight_cycle': self.flight_cycle,
                                      'inner_sequence': inner, 'outer_sequence': outer,
                                      'packet_hex': packet.hex(),
                                      'wire_packet_hex': wire_packet.hex()}
                self.commands.append(command_record)
            slot['event'].wait(timeout)
            with self._lock:
                command_record['reply_hex'] = None if slot['payload'] is None else slot['payload'].hex()
                command_record['reply_observed_at'] = time.time() if slot['payload'] is not None else None
            return slot['payload']
        finally:
            with self._lock:
                self._pending.pop(inner, None)

    def request_native(self, receiver, command_set, command_id, payload, timeout=2., cancel_event=None):
        """Only the five capture-derived native Circle request shapes are allowed."""
        allowed = {(0x17, 3, 0xf9): CIRCLE_SELECTION_PAYLOAD,
                   (0x92, 3, 0xf9): None,
                   (0x92, 3, 0xf8): RADIUS_HASH_BYTES,
                   (0x92, 0x23, 0x13): CIRCLE_START_PAYLOAD,
                   (0x92, 0x23, 0x15): b''}
        key = (receiver, command_set, command_id)
        if key == (0x92, 3, 0xf9):
            if len(payload) != 8:
                raise ValueError('Unsupported radius payload')
            validate_radius(parse_radius_value(b'\x00' + payload))
        elif key not in allowed or allowed[key] != payload:
            raise ValueError('Unsupported native Circle request')
        if self.transport is None or not 0 < timeout <= 3:
            raise RuntimeError('Native requests require reliable transport and a bounded timeout')
        if key == (0x92, 0x23, 0x13) and (self.land_requested or self.land_sent):
            raise RuntimeError('本会话已请求降落；确认落地后退出重连，再启动环绕')
        return self._request_tunneled(receiver,command_set,command_id,payload,timeout,cancel_event,
                                      label='native_circle')

    def request_camera(self, command_id, payload, timeout=2., cancel_event=None):
        validate_camera_request(command_id,payload)
        if (not self.device_verified or self._stop.is_set()
                or time.monotonic()-self.last_receive_at>1):
            raise RuntimeError('相机控制需要已核验且新鲜的连接')
        return self._request_tunneled(1,2,command_id,payload,timeout,cancel_event,label='native_camera')

    def select_photo_context(self,timeout=2.,*,cancel_event=None):
        """Observed 0x17 parameter write preceding both successful Fly photo captures."""
        if (self.transport is None or not self.device_verified or self._stop.is_set()
                or time.monotonic()-self.last_receive_at>1):
            raise RuntimeError('拍照前置设置需要已核验且新鲜的可靠连接')
        with self._lock:
            flight=self._fresh_flight_locked()
            task=self.subject_observation.snapshot().get('task')
            grounded=bool(flight and flight.get('motors_on') is False and flight.get('airborne_flag') is False)
            native_ready=self._native_manual_ready_locked() if self.native_task_may_fly else False
            manual_hover=bool(flight and flight.get('motors_on') and flight.get('airborne_flag')
                              and flight.get('mode_raw')==6 and (native_ready or task and task.get('mode_raw')==9))
            if task and task.get('phase_raw') not in (None,0) and task.get('mode_raw') not in (0,9):
                raise RuntimeError('当前原生任务仍在执行，未切换拍照前置设置')
            if not grounded and (not manual_hover or self.native_task_may_fly and not native_ready):
                raise RuntimeError('拍照前置设置需先确认地面或手动悬停；原生任务中请先停止任务')
        return self._request_tunneled(0x17,3,0xf9,PHOTO_CONTEXT_PAYLOAD,timeout,cancel_event,label='camera_context')

    def list_media(self,timeout=6.,*,cancel_event=None):
        from media_directory import make_directory_request
        from media_directory_link import DirectoryStream
        if (not 0<timeout<=10 or self.transport is None or not self.device_verified
                or self._stop.is_set() or time.monotonic()-self.last_receive_at>1):
            raise RuntimeError('读取相册需要已核验且新鲜的可靠连接')
        if cancel_event is not None and cancel_event.is_set():raise RuntimeError('相册操作已取消')
        if not self._media_directory_lock.acquire(blocking=False):raise RuntimeError('已有相册读取正在进行')
        receiver=None
        try:
            self._media_directory_session=self._media_directory_session%65535+1
            file_session=self._media_directory_session
            with self._tx_lock:
                receiver=DirectoryStream(outer_session=self.session,file_session=file_session,
                    first_sequence=(self.transport.downlink_cursor+8)&0xffff)
                with self._lock:
                    self._media_directory_receiver=receiver
            deadline=time.monotonic()+timeout
            reply=self._request_tunneled(1,0,0x26,make_directory_request(file_session),
                min(2.,timeout),cancel_event,label='media_directory')
            if reply not in (None,b'\x00'):raise RuntimeError('相册读取被拒绝：'+reply.hex())
            while True:
                if cancel_event is not None and cancel_event.is_set():raise RuntimeError('相册操作已取消')
                if self._stop.is_set():raise RuntimeError('连接已停止，相册未完成')
                if receiver.done.is_set():break
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('相册数据未完整收到；未使用旧列表')
                receiver.done.wait(min(.05,remaining))
            if receiver.error:raise RuntimeError('相册解析失败：'+receiver.error)
            with self._lock:
                self.media_directory_entries=list(receiver.entries)
            return list(receiver.entries)
        finally:
            with self._lock:
                if self._media_directory_receiver is receiver:self._media_directory_receiver=None
            self._media_directory_lock.release()

    def _request_tunneled(self,receiver,command_set,command_id,payload,timeout,cancel_event,*,label):
        if self.transport is None or not 0<timeout<=3:
            raise RuntimeError('Native requests require reliable transport and a bounded timeout')
        key=(receiver,command_set,command_id)
        inner, outer = self._next_inner(), self._next_outer()
        template = dict(sender=2, receiver=receiver, flags=0x40, command_set=command_set,
                        command_id=command_id, tunneled=True)
        packet = encode_message(template, inner, outer, payload)
        slot = {'event': threading.Event(), 'payload': None, 'command_set': command_set,
                'command_id': command_id, 'sender': receiver}
        with self._lock:
            self._pending[inner] = slot
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('Native request canceled before send')
            if (command_set, command_id) == (0x23, 0x13):
                with self._lock:
                    self.native_task_may_fly = True
                    self.native_started_at = time.time()
                    self.native_stop_confirmed_at = None
            if key == (0x92, 0x23, 0x15):
                with self._lock:
                    self.native_stop_confirmed_at = None
            wire = self._send(packet, duml=True, cancel_event=cancel_event)
            record = dict(time=time.time(), command=label, receiver=receiver,
                          command_set=command_set, command_id=command_id, payload_hex=payload.hex(),
                          inner_sequence=inner, outer_sequence=outer, packet_hex=packet.hex(), wire_packet_hex=wire.hex())
            with self._lock:
                self.commands.append(record)
            slot['event'].wait(timeout)
            with self._lock:
                record['reply_hex'] = None if slot['payload'] is None else slot['payload'].hex()
                record['reply_observed_at'] = None if slot['payload'] is None else time.time()
                if key == (0x92, 0x23, 0x15):
                    if record['time'] >= (self.native_started_at or 0):
                        self.native_stop_confirmed_at = record['reply_observed_at'] if slot['payload'] == b'\x00' else None
            return slot['payload']
        finally:
            with self._lock:
                self._pending.pop(inner, None)

    def query_device(self, timeout=2.0):
        """Send the captured zero-payload device-information query only."""
        if self._stop.is_set() or time.monotonic() - self.last_receive_at > 1:
            raise RuntimeError('Drone downlink is not active')
        inner = self._next_inner()
        outer = self._next_outer()
        packet = build_query_packet(self.session, self.video_seed, self.video_ack,
                                    self.video_word, outer, inner)
        slot = {'event': threading.Event(), 'payload': None,
                'command_set': 3, 'command_id': 0x34}
        with self._lock:
            self._pending[inner] = slot
        try:
            wire_packet = self._send(packet)
            with self._lock:
                self.commands.append({'time': time.time(), 'command': 'device_query',
                                      'inner_sequence': inner, 'outer_sequence': outer,
                                      'packet_hex': packet.hex(),
                                      'wire_packet_hex': wire_packet.hex()})
            slot['event'].wait(timeout)
            reply = slot['payload']
            self.device_verified = bool(reply and reply[0] == 0)
            if self.device_verified:
                self.device_reply = reply
            return reply
        finally:
            with self._lock:
                self._pending.pop(inner, None)

    def probe_startup_queries(self, timeout=2.0):
        """Send only the four captured zero-payload startup queries."""
        if self._stop.is_set() or time.monotonic() - self.last_receive_at > 1:
            raise RuntimeError('Drone downlink is not active')
        first = self._reserve_inner(4)
        reply_sequence = (first + 1) & 0xffff
        packet = build_startup_query_packet(self.session, self.video_seed,
                                            self.video_ack, self.video_word, first)
        slot = {'event': threading.Event(), 'payload': None,
                'command_set': 0, 'command_id': 1, 'sender': 0x1f}
        with self._lock:
            self._pending[reply_sequence] = slot
        try:
            wire_packet = self._send(packet)
            with self._lock:
                self.commands.append({'time': time.time(), 'command': 'startup_queries',
                                      'inner_sequence': first,
                                      'packet_hex': packet.hex(),
                                      'wire_packet_hex': wire_packet.hex()})
            slot['event'].wait(timeout)
            return slot['payload']
        finally:
            with self._lock:
                self._pending.pop(reply_sequence, None)

    def initialize_control_link(self, timeout=8.0):
        """Wait for actual device and fresh-video evidence; never send flight actions."""
        if not 0 < timeout <= 15:
            raise ValueError('Initialization timeout outside bounds')
        deadline = time.monotonic() + timeout
        self.probe_startup_queries(timeout=min(0.5, timeout))
        while not self._stop.is_set() and time.monotonic() < deadline:
            if not self.device_verified:
                self.query_device(timeout=min(1.0, max(0, deadline - time.monotonic())))
            if (self.device_verified and self.last_video_at is not None
                    and time.monotonic() - self.last_video_at <= 1):
                return True
            self._stop.wait(min(0.1, max(0, deadline - time.monotonic())))
        return False

    def _check_takeoff_link(self):
        if self._stop.is_set():
            raise RuntimeError('连接已停止，请退出后重新连接')
        if self.transport is not None and not self.device_verified:
            raise RuntimeError('Flight-controller device reply is not verified')
        if time.monotonic() - self.last_receive_at > 1:
            raise RuntimeError('Drone downlink has been silent for more than one second')
        if self.last_video_at is None or time.monotonic() - self.last_video_at > 1:
            raise RuntimeError('Video stream has been silent for more than one second')

    def takeoff(self, timeout=2.0, *, cancel_event=None):
        if self.native_task_may_fly:
            raise RuntimeError('原生任务可能仍控制飞机；请先降落，再确认已落地后再次起飞或退出重连')
        if self.takeoff_sent or self.land_requested or self.land_sent:
            raise RuntimeError('本轮已起飞或已进入降落；确认已落地后可输入“再次起飞”')
        self._check_takeoff_link()
        with self._lock:
            if (self.native_task_may_fly or self.takeoff_sent or self.land_requested
                    or self.land_sent or self._flight_cancel.is_set()):
                raise RuntimeError('飞行状态已变化或已请求降落；起飞已取消')
            self.landing_status.new_flight()
            self.takeoff_started_at = time.time()
            self.takeoff_sent = True
            self.flight_cycle += 1
        reply = self._flight_command(1, timeout, cancel_event=cancel_event)
        self.takeoff_acknowledged = reply == b'\x00'
        return reply

    def takeoff_again(self, *, confirmed_grounded=False, timeout=2.0, cancel_event=None):
        self.prepare_grounded_cycle(confirmed_grounded=confirmed_grounded)
        return self.takeoff(timeout, cancel_event=cancel_event)

    def _fresh_flight_locked(self):
        latest = self.landing_status.latest
        return latest if latest and 0 <= time.time() - latest['at'] <= 1 else None

    def _native_manual_ready_locked(self):
        stopped = self.native_stop_confirmed_at
        flight = self._fresh_flight_locked()
        task = self.subject_observation.snapshot().get('task')
        return bool(stopped is not None and stopped >= (self.native_started_at or 0)
                    and flight and flight['at'] >= stopped
                    and flight.get('motors_on') and flight.get('airborne_flag') and flight.get('mode_raw') == 6
                    and task and task['at'] >= stopped and task.get('payload_hex') in ('0000000101', '0000000102'))

    def prepare_grounded_cycle(self, *, confirmed_grounded=False):
        """Explicit operator confirmation plus fresh motors-off OSD, never an ACK alone."""
        if not confirmed_grounded:
            raise ValueError('必须先目视确认飞机已落地')
        self._check_takeoff_link()
        if not self._movement_lock.acquire(blocking=False):
            raise RuntimeError('上个动作尚未结束，请稍后再起飞')
        try:
            self.center_sticks()
            with self._lock:
                flight = self._fresh_flight_locked()
                since = max(self.native_started_at or 0, self.takeoff_started_at or 0,
                            self.landing_status.requested_at or 0)
                if (not flight or flight['at'] < since or flight.get('motors_on') is not False
                        or flight.get('airborne_flag') is not False):
                    raise RuntimeError('尚无新鲜的电机停止与落地状态；未开始新一轮飞行')
                if self.native_task_may_fly:
                    task = self.subject_observation.snapshot().get('task')
                    if (not task or task['at'] < since or task.get('payload_hex') not in
                            ('0000000100', '0000000101', '0000000102', '0300000100')):
                        raise RuntimeError('原生任务尚未确认结束；未开始新一轮飞行')
                if self._watchdog is not None:
                    self._watchdog.cancel()
                    self._watchdog = None
                self.takeoff_sent = self.takeoff_acknowledged = False
                self.takeoff_started_at = None
                self.native_task_may_fly = False
                self.native_started_at = self.native_stop_confirmed_at = None
                self.land_requested = self.land_sent = self.land_acknowledged = False
                self._flight_cancel = threading.Event()
                self.auto_land_sent = False
                self.landing_status.new_flight()
        finally:
            self._movement_lock.release()

    def _auto_land(self, expected_cycle=None):
        if expected_cycle is not None and expected_cycle != self.flight_cycle:
            return
        if not self.land_sent and not self._stop.is_set():
            self.auto_land_sent = True
            print('\n定时降落已触发，正在发送降落。')
            try:
                self.land(timeout=0.3)
            except (OSError, RuntimeError):
                pass

    def move(self, direction, seconds=0.5, *, cancel_event=None):
        if direction not in MOVEMENTS:
            raise ValueError('Unknown movement direction')
        validate_duration(seconds)
        with self._lock:
            native = getattr(self, 'native_task_may_fly', False)
            native_ready = self._native_manual_ready_locked() if native else False
        if native and not native_ready:
            raise RuntimeError('原生任务停止和悬停状态尚未确认；先说停止环绕并等待新鲜悬停状态')
        if not native and not self.takeoff_acknowledged:
            raise RuntimeError('方向动作需先收到起飞确认')
        if self.land_requested or self.land_sent:
            raise RuntimeError('已开始降落，方向动作已锁定')
        if self._stop.is_set():
            raise RuntimeError('Connection stopped: ' + str(self.last_error))
        if time.monotonic() - self.last_receive_at > 1:
            raise RuntimeError('Drone downlink has been silent for more than one second')
        if not self._movement_lock.acquire(blocking=False):
            raise RuntimeError('已有方向动作正在执行')
        entry = None
        try:
            with self._stick_tx_lock:
                with self._lock:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError('动作已取消')
                    if self.land_requested or self.land_sent:
                        raise RuntimeError('已开始降落，方向动作已锁定')
                    if getattr(self, 'native_task_may_fly', False) and not self._native_manual_ready_locked():
                        raise RuntimeError('原生任务尚未交还方向控制')
                    self.roll = self.pitch = self.throttle = self.yaw = 1024
                    _, axis, value = MOVEMENTS[direction]
                    setattr(self, axis, value)
                    self._movement_cancel.clear()
                    entry = {'time': time.time(), 'direction': direction,
                             'seconds': seconds, 'completed': False}
                    self.movements.append(entry)
                self._send_stick()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError('动作已取消，四轴回中')
                if self._movement_cancel.wait(min(0.05, max(0, deadline - time.monotonic()))):
                    raise RuntimeError('动作已取消，四轴回中')
                if self._stop.is_set():
                    raise RuntimeError('Connection stopped during movement: ' + str(self.last_error))
                if time.monotonic() - self.last_receive_at > 1:
                    raise RuntimeError('Drone downlink stopped during movement')
            entry['completed'] = True
        finally:
            try:
                if entry is not None:
                    self.center_sticks()
                    with self._lock:
                        entry['finished_at'] = time.time()
            finally:
                self._movement_lock.release()

    def forward(self, seconds=1.0):
        return self.move('forward', seconds)

    def land(self, timeout=2.0):
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        with self._lock:
            self.land_requested = True
            self._flight_cancel.set()
            self.landing_status.begin()
        try:
            self.center_sticks()
        except (OSError, RuntimeError):
            # A failed neutral-stick send must not suppress the land request.
            pass
        reply = self._flight_command(2, timeout)
        self.land_acknowledged = reply == b'\x00'
        return reply

    def status(self):
        with self._lock:
            subject_status = self.subject_observation.snapshot()
            camera_status = self.camera_observation.snapshot()
            native_manual_ready = self._native_manual_ready_locked() if self.native_task_may_fly else False
        return {'session': f'0x{self.session:04x}',
                'transport_mode': 'reliable' if self.transport is not None else 'legacy',
                'transport': self.transport.status() if self.transport is not None else None,
                'registration': self.registration.status() if self.registration is not None else None,
                'routing': self.routing_sequences.status() if self.routing_sequences is not None else None,
                'device_verified': self.device_verified,
                'video_seed': f'0x{self.video_seed:04x}',
                'video_ack': f'0x{self.video_ack:04x}',
                'received': self.received,
                'last_downlink_age_s': round(time.monotonic() - self.last_receive_at, 2),
                'last_video_age_s': (None if self.last_video_at is None else
                                     round(time.monotonic() - self.last_video_at, 2)),
                'takeoff_acknowledged': self.takeoff_acknowledged,
                'takeoff_sent': self.takeoff_sent,
                'native_task_may_fly': self.native_task_may_fly,
                'native_stop_confirmed_at': self.native_stop_confirmed_at,
                'native_manual_ready': native_manual_ready,
                'land_requested': self.land_requested,
                'video_preview': (self.video_receiver.status() if self.video_receiver is not None else None),
                'video_preview_error': self.video_receiver_error,
                'udp_receive_buffer_bytes': self.udp_receive_buffer_bytes,
                'flight_cycle': self.flight_cycle,
                'auto_land_after_s': self.max_flight_seconds,
                'axes': {'roll': self.roll, 'pitch': self.pitch,
                         'throttle': self.throttle, 'yaw': self.yaw},
                'land_sent': self.land_sent, 'land_acknowledged': self.land_acknowledged,
                'landing_observation': self.landing_status.snapshot(),
                'subject_observation': subject_status,
                'camera': camera_status,
                'media_directory_count': len(self.media_directory_entries),
                'auto_land_sent': self.auto_land_sent,
                'error': self.last_error}

    def save_log(self):
        captures = Path(__file__).resolve().parents[1] / 'captures'
        captures.mkdir(exist_ok=True)
        output = captures / f'control-attempt-{datetime.datetime.now():%Y%m%d-%H%M%S}.json'
        status = self.status()
        with self._lock:
            record = {'status': status, 'commands': list(self.commands),
                      'movements': list(self.movements),
                      'outgoing_transport': list(self.outgoing),
                      'incoming': list(self.events),
                      'incoming_truncated': self.received > len(self.events),
                      'incoming_control': list(self.control_events),
                      'incoming_control_truncated':
                          self.control_received > len(self.control_events),
                      'flight_status_history': list(self.landing_status.history),
                      'subject_observation_history': list(self.subject_observation.history)}
            record['camera_state_history'] = list(self.camera_observation.history)
            record['media_directory_entries'] = list(self.media_directory_entries)
            record['incoming_media_data']=list(self.media_data_events)
            record['incoming_media_data_truncated']=self.media_data_received>len(self.media_data_events)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return output

    def close(self):
        if self._watchdog is not None:
            self._watchdog.cancel()
        try:
            self.center_sticks()
        except (OSError, RuntimeError):
            pass
        if self._stop.is_set():
            self.sock.close()
            return
        if (self.takeoff_sent or self.native_task_may_fly) and not self.land_sent:
            try:
                self.land(timeout=0.3)
            except (OSError, RuntimeError):
                pass
        self._stop.set()
        self._ticker.join(timeout=1)
        self._reader.join(timeout=1)
        self.sock.close()




