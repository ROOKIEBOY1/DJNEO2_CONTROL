import hashlib
import itertools
import json
import unittest

from messages import duml_messages
from control_packets import valid_frames
from duml import encode_frame
from registration_exchange import PROFILE, RegistrationExchange


def logical_frames(messages):
    for raw in messages:
        if raw[4:6] == b'\x3b\xe9':
            yield from valid_frames(raw[11:-2])
        else:
            yield raw


class RegistrationTests(unittest.TestCase):


    def test_unknown_request_does_not_receive_a_frozen_response(self):
        exchange = RegistrationExchange(started=0)
        exchange._schedule = []
        exchange._heartbeat_at = 100
        exchange.observe(encode_frame(0xe9, 0xee, 9, 0x40, 0x51, 0x41, b'unknown'))
        self.assertEqual(exchange.poll(0, lambda: 1, lambda: 2), [])

    def test_schedule_has_no_flight_or_parameter_write_and_heartbeat_is_live(self):
        exchange = RegistrationExchange(started=0)
        sequence = itertools.count(100).__next__
        first = list(logical_frames(exchange.poll(0, sequence, sequence)))
        last = list(logical_frames(exchange.poll(2, sequence, sequence)))
        for frame in first + last:
            self.assertIn(frame[9:11], (b'\x07\x93', b'\x51\x13', b'\x51\x34',
                                       b'\x51\x02', b'\x51\x06', b'\x51\x41'))
            self.assertEqual(len(list(valid_frames(frame))), 1)
        heartbeats = [f[11:-2] for f in first + last if f[9:11] == b'\x51\x13']
        self.assertEqual(len(heartbeats), 2)
        self.assertEqual(int.from_bytes(heartbeats[1][39:43], 'little'), 1)
        self.assertEqual(int.from_bytes(heartbeats[1][43:47], 'little'), 2000)


