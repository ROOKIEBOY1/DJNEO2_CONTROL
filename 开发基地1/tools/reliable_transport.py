"""Bounded reliable UDP transport, independently built from observed wire fields.

Neo 2 captures establish the three window positions in type-4 status packets.
The type-5 uplink format is a hypothesis informed by the first-generation
neo-control implementation; aircraft acceptance must be checked separately.
"""

from collections import OrderedDict

from capture_protocol import parse_outer


def reached(cursor, sequence):
    return ((cursor - sequence) & 0xffff) < 0x8000


def header(session, kind, length, sequence=0):
    result = bytearray(16)
    result[:2] = (0x8000 | length).to_bytes(2, 'little')
    result[2:4] = session.to_bytes(2, 'little')
    result[4:6] = sequence.to_bytes(2, 'little')
    result[6] = kind
    for value in result[:7]:
        result[7] ^= value
    return result


class ReliableTransport:
    def __init__(self, session, base, retry_interval=0.15, lifetime=1.0):
        if not 1 <= session <= 0xffff or not 0 <= base <= 0xffff or base & 7:
            raise ValueError('Invalid session or aligned transport base')
        self.session = session
        self.base = base
        self.previous = base
        self.next_sequence = (base + 8) & 0xffff
        self.counter = 1
        self.video_cursor = base
        self.downlink_cursor = base
        self.peer_ack = base
        self.sent = 0
        self.acknowledged = 0
        self.retransmitted = 0
        self.pending = OrderedDict()
        self._downlink_seen = set()
        self.retry_interval = retry_interval
        self.lifetime = lifetime

    def encode(self, duml, now):
        if not duml or len(duml) + 20 > 1472:
            raise ValueError('Reliable payload outside observed MTU')
        if len(self.pending) >= 32:
            raise RuntimeError('Reliable send window is full')
        sequence = self.next_sequence
        packet = header(self.session, 5, 20 + len(duml), sequence)
        packet[8:10] = self.previous.to_bytes(2, 'little')
        packet[10:12] = sequence.to_bytes(2, 'little')
        packet += bytes((self.counter, 1, 0, 0)) + duml
        raw = bytes(packet)
        self.pending[sequence] = [raw, now, now, 1]
        self.previous = sequence
        self.next_sequence = (sequence + 8) & 0xffff
        self.counter = (self.counter + 1) & 0xff
        self.sent += 1
        return raw

    def observe(self, raw):
        outer = parse_outer(raw)
        if outer.session != self.session:
            return
        if outer.channel == 2 and reached(outer.sequence, self.video_cursor):
            self.video_cursor = outer.sequence
        elif outer.channel == 3:
            delta = (outer.sequence - self.downlink_cursor) & 0xffff
            if 0 < delta <= 8 * 128 and not delta & 7:
                self._downlink_seen.add(outer.sequence)
                while (self.downlink_cursor + 8) & 0xffff in self._downlink_seen:
                    self.downlink_cursor = (self.downlink_cursor + 8) & 0xffff
                    self._downlink_seen.remove(self.downlink_cursor)
        elif outer.channel == 1 and len(raw) >= 34:
            cursor = int.from_bytes(raw[24:26], 'little')
            # Reject stale or impossible acknowledgements beyond our sent data.
            if (not ((cursor - self.base) & 7) and reached(cursor, self.peer_ack)
                    and reached(self.previous, cursor)):
                self.peer_ack = cursor
                for sequence in list(self.pending):
                    if reached(cursor, sequence):
                        self.pending.pop(sequence)
                        self.acknowledged += 1
                    else:
                        break

    def acknowledgement(self):
        packet = header(self.session, 4, 34)
        packet[8:12] = self.video_cursor.to_bytes(2, 'little') * 2
        packet += self.downlink_cursor.to_bytes(2, 'little') * 2 + bytes(4)
        packet += self.previous.to_bytes(2, 'little') * 2 + bytes(6)
        return bytes(packet)

    def retries(self, now):
        result = []
        for sequence, item in self.pending.items():
            raw, first, last, attempts = item
            if now - first >= self.lifetime:
                raise TimeoutError(f'Aircraft did not acknowledge transport sequence 0x{sequence:04x}')
            if now - last >= self.retry_interval and len(result) < 8:
                item[2] = now
                item[3] = attempts + 1
                self.retransmitted += 1
                result.append(raw)
        return result

    def status(self):
        return {'sent': self.sent, 'acknowledged': self.acknowledged,
                'pending': len(self.pending), 'retransmitted': self.retransmitted,
                'peer_ack': f'0x{self.peer_ack:04x}',
                'downlink_ack': f'0x{self.downlink_cursor:04x}'}
