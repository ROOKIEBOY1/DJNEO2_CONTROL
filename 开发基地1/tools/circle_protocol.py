"""Capture-verified inner frames for Neo 2 Circle. Offline only; no socket API.

This does not implement a live Circle controller. Start, radius and manual
stop are reconstructed from this user's own labeled captures.
"""
import math
import struct
from offline_takeoff import encode_frame

RADIUS_HASH_BYTES = bytes.fromhex('46932703')
CIRCLE_START_PAYLOAD = bytes.fromhex('010500')
CIRCLE_SELECTION_PAYLOAD = bytes.fromhex('878867a305000000')

def validate_radius(radius_m):
    if not math.isfinite(radius_m) or not 2 <= radius_m <= 10:
        raise ValueError('环绕半径支持 2–10 米')
    return float(radius_m)

def radius_payload(radius_m):
    return RADIUS_HASH_BYTES + struct.pack('<f', validate_radius(radius_m))

def build_radius_frame(radius_m, sequence):
    return encode_frame(2, 0x92, sequence, 0x40, 3, 0xf9,
                        radius_payload(radius_m))

def build_circle_start_frame(sequence):
    return encode_frame(2, 0x92, sequence, 0x40, 0x23, 0x13, CIRCLE_START_PAYLOAD)

def build_circle_stop_frame(sequence):
    # The observed stop has ZERO payload bytes, not a zero-valued byte.
    return encode_frame(2, 0x92, sequence, 0x40, 0x23, 0x15, b'')

def build_circle_selection_frame(sequence):
    return encode_frame(2, 0x17, sequence, 0x40, 3, 0xf9, CIRCLE_SELECTION_PAYLOAD)

def parse_radius_value(payload):
    # In these batch read replies the radius hash is the FIRST returned entry;
    # searching arbitrary bytes could mistake a value for another parameter ID.
    if len(payload) < 9 or payload[:5] != b'\x00' + RADIUS_HASH_BYTES:
        raise ValueError('Not a successful radius-first parameter reply')
    value = struct.unpack_from('<f', payload, 5)[0]
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid radius value')
    return value

def deduplicate_records(records):
    # iOS can expose the same frame twice with timestamp rounding differences
    # around one microsecond. Preserve later retransmissions, merge <=1 ms only.
    fields = ('side', 'session', 'sender', 'receiver', 'command_set',
              'command_id', 'sequence', 'flags', 'payload', 'tunneled')
    seen, unique = {}, []
    for record in sorted(records, key=lambda record: record['at']):
        key = tuple(record.get(name) for name in fields)
        if key not in seen or record['at'] - seen[key] > .001:
            seen[key] = record['at']
            unique.append(record)
    return unique

def matching_reply(request, records):
    for reply in records:
        if (reply['session'] == request['session'] and reply['sequence'] == request['sequence']
                and reply['sender'] == request['receiver'] and reply['receiver'] == request['sender']
                and reply['command_set'] == request['command_set'] and reply['command_id'] == request['command_id']
                and reply['flags'] & 0x80 and 0 <= reply['at'] - request['at'] <= 3):
            return reply
    return None

if __name__ == '__main__':
    print('Offline preview only; inner frames are not complete network packets.')
    print('Observed 2 m radius:', build_radius_frame(2, 1).hex())
    print('Observed mode selection:', build_circle_selection_frame(2).hex())
    print('Observed Circle start:', build_circle_start_frame(3).hex())
    print('Observed task stop:', build_circle_stop_frame(4).hex())
