import socket
import struct
import threading
import unittest
from unittest.mock import Mock
from collections import deque
from capture_messages import duml_messages
from flight_console import FlightController
from handshake_probe import build_handshake
from offline_takeoff import encode_frame
from reliable_transport import header, ReliableTransport

class NativeCircleWireTests(unittest.TestCase):
    def setUp(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server.bind(('127.0.0.1', 0))
        self.server.settimeout(.05)
        self.stop = threading.Event()
        self.requests = []
        def serve():
            directory_cursor=0x5ef8
            directory_message_sequence=0
            while not self.stop.is_set():
                try:
                    raw, peer = self.server.recvfrom(4096)
                except (socket.timeout,ConnectionResetError):
                    continue
                if raw == build_handshake(0xebdf, 0x5ef8):
                    self.server.sendto(bytes.fromhex('0f80dfeb000000bb010f000505401f'), peer)
                elif len(raw) >= 20 and raw[6] == 5:
                    ack = header(0xebdf, 1, 34)+bytes(18)
                    ack[24:28] = raw[4:6]*2
                    self.server.sendto(bytes(ack), peer)
                    for f, _ in duml_messages(raw):
                        if f[4] != 2 or f[5] not in (1, 0x17, 0x92):
                            continue
                        self.requests.append(f)
                        payload = b'\x00'+f[11:-2] if f[10] == 0xf9 else b'\x00'
                        if f[9:11] == b'\x03\xf8':
                            payload += f[11:-2]+struct.pack('<f', 2.)
                        response = encode_frame(f[5], 2, int.from_bytes(f[6:8], 'little'),
                                                0xc0, f[9], f[10], payload)
                        packet = header(0xebdf, 1, 34+len(response))+bytes(16)+len(response).to_bytes(2, 'little')+response
                        packet[24:28] = raw[4:6]*2
                        self.server.sendto(bytes(packet), peer)
                        if f[5]==1 and f[9:11]==b'\x00\x26':
                            album_session=f[15:17]
                            payload=b'\x4a\x01'+struct.pack('<H',0x1012)+album_session+bytes(4)+bytes(8)
                            directory=encode_frame(0xe1,2,directory_message_sequence,0,0,0x27,payload)
                            directory_message_sequence=(directory_message_sequence+1)&0xffff
                            directory_cursor=(directory_cursor+8)&0xffff
                            data=header(0xebdf,3,20+len(directory),directory_cursor)+bytes(4)+directory
                            self.server.sendto(bytes(data),peer)
        self.worker = threading.Thread(target=serve, daemon=True)
        self.worker.start()
        self.controller = FlightController.connect('127.0.0.1', '127.0.0.1', self.server.getsockname()[1],
            0xebdf, 0x5ef8, attempts=1, retry_interval=.1, prepare_seconds=0, reliable=True)

    def tearDown(self):
        self.controller.close()
        self.stop.set()
        self.worker.join(1)
        self.server.close()

    def test_custom_receiver_replies_and_actual_payloads_cross_reliable_wire(self):
        for receiver, cs, ci, payload in ((0x17,3,0xf9,bytes.fromhex('878867a305000000')),
                (0x92,3,0xf9,bytes.fromhex('4693270300000040')),
                (0x92,3,0xf8,bytes.fromhex('46932703')),
                (0x92,0x23,0x13,b'\x01\x05\x00'), (0x92,0x23,0x15,b'')):
            reply = self.controller.request_native(receiver, cs, ci, payload, timeout=.5)
            self.assertIsNotNone(reply)
            self.assertEqual(reply[0], 0)
            if (cs, ci) == (0x23, 0x13):
                self.assertTrue(self.controller.native_task_may_fly)
        self.assertEqual(self.requests[-1][11:-2], b'')
        self.assertEqual(self.requests[-1][9:11], b'\x23\x15')
        self.assertTrue(all(c['reply_hex'] is not None for c in self.controller.commands))

    def test_guessed_requests_and_canceled_start_do_not_reach_wire(self):
        with self.assertRaises(ValueError):
            self.controller.request_native(0x92,0x23,0x15,b'\x00')
        canceled = threading.Event()
        canceled.set()
        with self.assertRaises(RuntimeError):
            self.controller.request_native(0x92,0x23,0x13,b'\x01\x05\x00',cancel_event=canceled)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.controller._pending, {})
        self.assertFalse(self.controller.native_task_may_fly)

    def test_camera_requests_use_camera_receiver_and_do_not_change_flight_flags(self):
        self.controller.device_verified = True
        for command,payload in ((0xff,bytes.fromhex('003700010100')),(1,b'\x01'),
                                (0xff,bytes.fromhex('003700020000')),(2,b'\x01'),(2,b'\x00')):
            self.assertEqual(self.controller.request_camera(command,payload,timeout=.5),b'\x00')
        self.assertTrue(all(frame[5] == 1 and frame[9] == 2 for frame in self.requests))
        self.assertFalse(self.controller.native_task_may_fly)
        self.assertFalse(self.controller.takeoff_sent)
        self.assertEqual(self.controller.commands[-1]['command'],'native_camera')
        with self.assertRaises(ValueError):self.controller.request_camera(2,b'\x02')

    def test_photo_context_uses_capture_observed_parameter_echo_without_starting_flight(self):
        from test_landing_status import osd
        from native_camera import PHOTO_CONTEXT_PAYLOAD
        self.controller.device_verified=True
        with self.controller._lock:self.controller.landing_status.observe(osd(flags=0))
        reply=self.controller.select_photo_context(timeout=.5)
        self.assertEqual(reply,b'\x00'+PHOTO_CONTEXT_PAYLOAD)
        self.assertEqual((self.requests[-1][5],self.requests[-1][9:11]),(0x17,b'\x03\xf9'))
        self.assertFalse(self.controller.native_task_may_fly)
        self.assertFalse(self.controller.takeoff_sent)

    def test_photo_context_cannot_replace_an_active_native_circle(self):
        from test_landing_status import osd
        self.controller.device_verified=True
        self.controller.native_task_may_fly=True
        with self.controller._lock:self.controller.landing_status.observe(osd(mode=37,flags=12))
        with self.assertRaises(RuntimeError):self.controller.select_photo_context(timeout=.1)
        self.assertEqual(self.requests,[])

    def test_photo_context_accepts_verified_manual_hover_after_circle_stop(self):
        import time
        from test_landing_status import osd
        self.controller.device_verified=True
        self.controller.native_task_may_fly=True
        self.controller.native_started_at=time.time()-2
        self.controller.native_stop_confirmed_at=time.time()-1
        with self.controller._lock:
            self.controller.landing_status.observe(osd(mode=6,flags=0x1c))
            self.controller.subject_observation.observe(encode_frame(0x92,2,1,0,0x23,0x14,bytes.fromhex('0000000101')))
        self.assertEqual(self.controller.select_photo_context(timeout=.5)[0],0)
        self.assertTrue(self.controller.native_task_may_fly)

    def test_media_directory_receives_the_separate_reliable_data_channel(self):
        self.controller.device_verified=True
        self.assertEqual(self.controller.list_media(timeout=.5),[])
        self.assertEqual(self.controller.list_media(timeout=.5),[])
        self.assertEqual(self.requests[-1][9:11],b'\x00\x26')
        self.assertNotEqual(self.requests[-1][15:17],self.requests[-2][15:17])
        self.assertFalse(self.controller.native_task_may_fly)
        self.assertFalse(self.controller.takeoff_sent)

    def test_cancelled_directory_request_sends_nothing(self):
        self.controller.device_verified=True
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(RuntimeError):self.controller.list_media(timeout=.1,cancel_event=cancelled)
        self.assertEqual(self.requests,[])

    def test_native_flight_does_not_reuse_a_previous_takeoff_ack_for_manual_movement(self):
        self.controller.takeoff_acknowledged = True
        self.controller.native_task_may_fly = True
        with self.assertRaisesRegex(RuntimeError, '原生任务'):
            self.controller.move('forward', .1)
        with self.assertRaisesRegex(RuntimeError, '原生任务'):
            self.controller.takeoff(timeout=.1)
        self.assertEqual(self.controller.movements, [])
        self.assertEqual(self.requests, [])

    def test_variable_radius_whitelist_accepts_only_correct_bounded_payloads(self):
        from circle_protocol import radius_payload
        payload = radius_payload(5.)
        self.assertEqual(self.controller.request_native(0x92,3,0xf9,payload,timeout=.5), b'\x00'+payload)
        for invalid in (b'xxxx'+struct.pack('<f',5), bytes.fromhex('46932703')+struct.pack('<f',float('nan')),
                        bytes.fromhex('46932703')+struct.pack('<f',11)):
            with self.assertRaises(ValueError):
                self.controller.request_native(0x92,3,0xf9,invalid,timeout=.1)

    def test_previous_landing_cycle_cannot_start_a_new_native_flight(self):
        self.controller.land_requested = self.controller.land_sent = True
        self.controller.land_acknowledged = True
        with self.assertRaisesRegex(RuntimeError, '退出重连'):
            self.controller.request_native(0x92,0x23,0x13,b'\x01\x05\x00',timeout=.1)
        self.assertFalse(self.controller.native_task_may_fly)
        self.assertEqual(self.requests, [])

    def test_full_transport_window_reports_stop_failure_instead_of_acknowledging(self):
        link = FlightController.__new__(FlightController)
        link._stop = threading.Event()
        link._lock = threading.Lock()
        link._tx_lock = threading.Lock()
        link.session, link.inner_sequence, link.outer_sequence = 1, 1, 1
        link.transport = ReliableTransport(1, 0x1000)
        link.routing_sequences = None
        link._pending, link.commands = {}, []
        link.sock = Mock()
        link.remote = ('127.0.0.1', 9003)
        link.outgoing = deque()
        for _ in range(32):
            link.transport.encode(b'full-window-test', 0)
        with self.assertRaisesRegex(RuntimeError, 'window is full'):
            link.request_native(0x92, 0x23, 0x15, b'')
        link.sock.sendto.assert_not_called()
        self.assertEqual(link._pending, {})
        self.assertEqual(link.commands, [])
