"""Capture-derived application registration for this user's aircraft.

Only the listed identity/registration messages are supported. Responses use
the current aircraft request sequence; no flight or parameter-write replay.
"""

import json
import hashlib
import threading
from collections import Counter
from pathlib import Path

from control_packets import OBSERVED_TRAILER_PREFIX
from fly_command import TRAILER_SUFFIX
from offline_takeoff import encode_frame
from registration_schema import channel_acknowledgement, identity_acknowledgement


PROFILE = Path(__file__).resolve().parents[1] / 'profiles' / 'neo2-registration.json'


def encode_message(template, sequence, outer_sequence, payload=None):
    frame = encode_frame(template['sender'], template['receiver'], sequence,
                         template['flags'], template['command_set'],
                         template['command_id'], bytes.fromhex(template['payload_hex'])
                         if payload is None else payload)
    if template['tunneled']:
        trailer = OBSERVED_TRAILER_PREFIX + outer_sequence.to_bytes(2, 'little') + TRAILER_SUFFIX
        frame = encode_frame(0x3b, 0xe9, outer_sequence, 0, 0x51, 1, frame + trailer)
    return frame


class RegistrationExchange:
    def __init__(self, profile_path=PROFILE, started=0):
        profile = json.loads(Path(profile_path).read_text(encoding='utf-8'))
        self.templates = profile['templates']
        self.response_rules = profile['response_rules']
        self.started = started
        self._lock = threading.Lock()
        self._responses = []
        self._schedule = [(0, 'register'), (0.05, 'info_direct'),
                          (0.15, 'open'), (0.30, 'identity'),
                          (0.45, 'capabilities'), (0.65, 'info_tunnel'),
                          (1.8, 'info_tunnel')]
        self._heartbeat_at = started
        self.counter = 0
        self.remote_state = b'\x01\x01\x01'
        self.remote_tail = bytes(3)
        self.sent = 0
        self.responses = 0
        self.requests_seen = 0
        self.schema_responses = 0
        self.unknown_requests = Counter()

    def observe(self, frame):
        if len(frame) < 13 or frame[4:6] != b'\xe9\xee' or frame[9] != 0x51:
            return
        payload = frame[11:-2]
        with self._lock:
            if frame[10] == 0x13 and len(payload) == 55:
                self.remote_state = payload[24:27]
                self.remote_tail = payload[52:55]
                return
            if frame[8] != 0x40:
                return
            key = f'{frame[10]:02x}:' + hashlib.sha256(payload).hexdigest()
            template_name = self.response_rules.get(key)
            template = self.templates.get(template_name) if template_name else None
            response_payload = None
            try:
                if frame[10] == 0x41:
                    candidate = self.templates['response_41:65:0002']
                    response_payload = channel_acknowledgement(
                        payload, bytes.fromhex(candidate['payload_hex']))
                    template = candidate
                elif frame[10] == 6:
                    candidate = self.templates['response_06:51:0000']
                    response_payload = identity_acknowledgement(
                        payload, bytes.fromhex(candidate['payload_hex']))
                    template = candidate
            except ValueError:
                response_payload = None
            if template is not None:
                self.requests_seen += 1
                self.schema_responses += response_payload is not None
                if len(self._responses) < 32:
                    self._responses.append((template, int.from_bytes(frame[6:8], 'little'),
                                            response_payload))
            else:
                self.unknown_requests[f'{frame[10]:02x}:{len(payload)}:{key[-64:-52]}'] += 1

    def poll(self, now, next_sequence, next_outer):
        result = []
        with self._lock:
            while self._responses:
                template, sequence, payload = self._responses.pop(0)
                result.append(encode_message(template, sequence, next_outer(), payload))
                self.responses += 1
            while self._schedule and now - self.started >= self._schedule[0][0]:
                _, name = self._schedule.pop(0)
                result.append(encode_message(self.templates[name], next_sequence(), next_outer()))
            if now >= self._heartbeat_at:
                payload = bytearray.fromhex(self.templates['heartbeat']['payload_hex'])
                payload[24:27] = self.remote_state
                payload[39:43] = (self.counter & 0xffffffff).to_bytes(4, 'little')
                payload[43:47] = (int((now - self.started) * 1000) & 0xffffffff).to_bytes(4, 'little')
                payload[52:55] = self.remote_tail
                result.append(encode_message(self.templates['heartbeat'], next_sequence(),
                                             next_outer(), bytes(payload)))
                self.counter += 1
                self._heartbeat_at = now + 0.2
            self.sent += len(result)
        return result

    def status(self):
        with self._lock:
            return {'sent': self.sent, 'requests_seen': self.requests_seen,
                    'responses': self.responses, 'remote_state': self.remote_state.hex(),
                    'schema_responses': self.schema_responses,
                    'unknown_requests': dict(self.unknown_requests)}
