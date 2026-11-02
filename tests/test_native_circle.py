import struct
import threading
import time
import unittest
from native_circle import NativeCircle

class FakeLink:
    def __init__(self):
        self.calls = []
        self.ready = True
        self.native_task_may_fly = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_start = False
        self.fail_stop = False
        self.radius = 2.

    def status(self):
        return dict(device_verified=self.ready, transport_mode='reliable', last_downlink_age_s=.01,
                    last_video_age_s=.01, subject_observation={'task': None})

    def request_native(self, receiver, cs, ci, payload, timeout=2, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('canceled')
        self.calls.append((receiver, cs, ci, payload))
        if (cs, ci) == (0x23, 0x13) and self.block_start:
            self.started.set()
            self.release.wait(1)
        if (cs, ci) == (0x23, 0x15):
            self.release.set()
            if self.fail_stop:
                raise RuntimeError('stop reply missing')
        if ci == 0xf9:
            if receiver == 0x92:
                self.radius = struct.unpack('<f', payload[4:])[0]
            return b'\x00'+payload
        if ci == 0xf8:
            return b'\x00'+payload+struct.pack('<f', self.radius)
        return b'\x00'

    def center_sticks(self):
        pass

    def land(self, timeout=2):
        self.calls.append(('land',))
        return b'\x00'

class NativeCircleTests(unittest.TestCase):
    def test_start_reply_is_not_reported_as_observed_execution(self):
        link = FakeLink()
        mission = NativeCircle(link)
        mission.prepare()
        mission.start(confirmed=True)
        outcome = mission.wait_execution(timeout=.02)
        self.assertFalse(outcome['executing'])
        self.assertIsNone(outcome['task'])
    def test_radius_readback_mismatch_blocks_start(self):
        link = FakeLink()
        original = link.request_native
        def request(receiver, cs, ci, payload, **kwargs):
            if ci == 0xf8:
                return b'\x00' + payload + struct.pack('<f', 2.)
            return original(receiver, cs, ci, payload, **kwargs)
        link.request_native = request
        mission = NativeCircle(link)
        with self.assertRaises(RuntimeError):
            mission.prepare(5.)
        self.assertFalse(mission.prepared)
        with self.assertRaises(RuntimeError):
            mission.start(confirmed=True)

    def test_prepare_radius_and_start_can_share_a_cancellation_token(self):
        mission = NativeCircle(FakeLink())
        token = threading.Event()
        self.assertAlmostEqual(mission.prepare(3.1, cancel_event=token), 3.1)
        self.assertEqual(mission.start(confirmed=True, cancel_event=token), b'\x00')
    def test_prepare_writes_and_reads_2m_without_starting(self):
        link = FakeLink()
        mission = NativeCircle(link)
        mission.prepare()
        self.assertTrue(mission.prepared)
        self.assertEqual([call[2] for call in link.calls], [0xf9, 0xf9, 0xf8])
        self.assertFalse(link.native_task_may_fly)

    def test_start_requires_prepare_confirmation_and_fresh_verified_link(self):
        link = FakeLink()
        mission = NativeCircle(link)
        with self.assertRaises(RuntimeError):
            mission.start(confirmed=True)
        mission.prepare()
        with self.assertRaises(ValueError):
            mission.start()
        link.ready = False
        with self.assertRaises(RuntimeError):
            mission.start(confirmed=True)
        link.ready = True
        self.assertEqual(mission.start(confirmed=True), b'\x00')
        self.assertTrue(link.native_task_may_fly)
        self.assertEqual(link.calls[-1], (0x92, 0x23, 0x13, b'\x01\x05\x00'))
        with self.assertRaises(RuntimeError):
            mission.start(confirmed=True)

    def test_stop_dispatches_while_start_waits_and_has_empty_payload(self):
        link = FakeLink()
        mission = NativeCircle(link)
        mission.prepare()
        link.block_start = True
        worker = threading.Thread(target=lambda: mission.start(confirmed=True))
        worker.start()
        self.assertTrue(link.started.wait(.5))
        began = time.monotonic()
        self.assertEqual(mission.stop(), b'\x00')
        self.assertLess(time.monotonic()-began, .2)
        worker.join(.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(link.calls[-1], (0x92, 0x23, 0x15, b''))

    def test_bad_radius_echo_does_not_prepare_or_start(self):
        link = FakeLink()
        link.request_native = lambda *args, **kwargs: b'\x00'
        mission = NativeCircle(link)
        with self.assertRaises(RuntimeError):
            mission.prepare()
        self.assertFalse(mission.prepared)

    def test_landing_is_attempted_even_if_task_stop_fails(self):
        link = FakeLink()
        mission = NativeCircle(link)
        link.fail_stop = True
        self.assertEqual(mission.land(), b'\x00')
        self.assertEqual(link.calls[-1], ('land',))

    def test_stopping_preparation_cancels_remaining_requests(self):
        link = FakeLink()
        mission = NativeCircle(link)
        original = link.request_native
        def request(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == 0x17:
                mission.stop()
            return result
        link.request_native = request
        with self.assertRaises(RuntimeError):
            mission.prepare()
        self.assertFalse(mission.prepared)
        self.assertFalse(any(call[:3] == (0x92, 3, 0xf9) for call in link.calls))

    def test_cancel_before_worker_entry_cannot_be_reset_by_prepare_or_start(self):
        link = FakeLink()
        mission = NativeCircle(link)
        canceled = threading.Event()
        canceled.set()
        with self.assertRaises(RuntimeError):
            mission.prepare(cancel_event=canceled)
        self.assertEqual(link.calls, [])
        mission.prepared = True
        with self.assertRaises(RuntimeError):
            mission.start(confirmed=True, cancel_event=canceled)
        self.assertEqual(link.calls, [])
        self.assertFalse(link.native_task_may_fly)

    def test_failed_stop_does_not_reuse_an_old_success_reply(self):
        link = FakeLink()
        mission = NativeCircle(link)
        mission.stop()
        self.assertEqual(mission.last_stop_reply, '00')
        link.fail_stop = True
        with self.assertRaises(RuntimeError):
            mission.stop()
        self.assertIsNone(mission.last_stop_reply)
