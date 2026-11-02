import hashlib
import itertools
import json
import unittest

from capture_messages import duml_messages, udp_records
from control_packets import valid_frames
from offline_takeoff import encode_frame
from registration_exchange import PROFILE, RegistrationExchange


def logical_frames(messages):
    for raw in messages:
        if raw[4:6] == b'\x3b\xe9':
            yield from valid_frames(raw[11:-2])
        else:
            yield raw


class RegistrationTests(unittest.TestCase):
    def test_changed_live_capability_and_identity_fields_receive_correct_responses(self):
        path = PROFILE.parent.parent / 'captures' / 'control-attempt-20261029-110016.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        exchange = RegistrationExchange(started=0)
        sequence = itertools.count(100).__next__
        checked = set()
        expected_names = {0x41: 'response_41:65:0002', 6: 'response_06:51:0000'}
        for row in record['incoming_control']:
            for frame, _ in duml_messages(bytes.fromhex(row['hex'])):
                if (frame[4:6] != b'\xe9\xee' or frame[9] != 0x51 or frame[8] != 0x40
                        or frame[10] not in expected_names or frame[10] in checked):
                    continue
                if len(frame) - 13 != (65 if frame[10] == 0x41 else 51):
                    continue
                exchange.observe(frame)
                responses = [f for f in logical_frames(exchange.poll(0, sequence, sequence))
                             if f[8] == 0xc0]
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0][6:8], frame[6:8])
                expected = bytes.fromhex(exchange.templates[expected_names[frame[10]]]['payload_hex'])
                self.assertEqual(responses[0][11:-2], expected)
                checked.add(frame[10])
        self.assertEqual(checked, {0x41, 6})

    def test_responses_match_a_second_recorded_connection(self):
        path = PROFILE.parents[0].parent / 'captures' / 'dji-fly-20261027-1949.pcapng'
        records = []
        start = None
        for timestamp, direction, raw in udp_records(path):
            if int.from_bytes(raw[2:4], 'little') != 0xd54f:
                continue
            messages = list(duml_messages(raw))
            if start is None and direction == 'app' and messages:
                start = timestamp
            if start is not None and timestamp > start + 4:
                break
            records.extend((timestamp, direction, frame) for frame, _ in messages)
        exchange = RegistrationExchange(started=0)
        sequence = itertools.count(100).__next__
        matched = 0
        for timestamp, direction, request in records:
            if direction != 'plane' or request[4:6] != b'\xe9\xee' or request[9] != 0x51:
                continue
            key = f'{request[10]:02x}:' + hashlib.sha256(request[11:-2]).hexdigest()
            if key not in exchange.response_rules:
                continue
            exchange.observe(request)
            generated = [f for f in logical_frames(exchange.poll(0, sequence, sequence))
                         if f[8] == 0xc0]
            self.assertEqual(len(generated), 1)
            response = generated[0]
            self.assertEqual(response[6:8], request[6:8])
            expected = [f for t, d, f in records if d == 'app' and
                        0 <= t - timestamp <= 1 and f[4:6] == b'\xee\xe9' and
                        f[6:8] == request[6:8] and f[9:11] == request[9:11] and
                        f[8] == 0xc0 and f[11:-2] == response[11:-2]]
            self.assertTrue(expected)
            matched += 1
        self.assertGreaterEqual(matched, 5)

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


if __name__ == '__main__':
    unittest.main()
