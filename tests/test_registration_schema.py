import json
import unittest

from messages import duml_messages, udp_records
from registration_exchange import PROFILE
from registration_schema import channel_acknowledgement, channel_offer, identity_acknowledgement


class RegistrationSchemaTests(unittest.TestCase):
    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_channel_lists_reproduce_recorded_phone_acknowledgements(self):
        profile = json.loads(PROFILE.read_text(encoding='utf-8'))
        template = bytes.fromhex(profile['templates']['response_41:65:0002']['payload_hex'])
        path = PROFILE.parent.parent / 'captures' / 'dji-fly-20261027-1949.pcapng'
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
            records.extend((timestamp, direction, f) for f, _ in messages)
        lengths = set()
        for timestamp, direction, request in records:
            payload = request[11:-2]
            if (direction != 'plane' or request[4:6] != b'\xe9\xee' or
                    request[9:11] != b'\x51\x41' or request[8] != 0x40
                    or payload[:3] != b'\x00\x02\x00'):
                continue
            predicted = channel_acknowledgement(payload, template)
            matches = [f for t, d, f in records if d == 'app' and
                       0 <= t - timestamp <= 1 and f[6:8] == request[6:8]
                       and f[9:11] == request[9:11] and f[8] == 0xc0
                       and f[11:-2] == predicted]
            self.assertTrue(matches)
            lengths.add(len(payload))
        self.assertEqual(lengths, {65, 35})

    def test_malformed_offer_is_rejected(self):
        with self.assertRaises(ValueError):
            channel_offer(b'\x00\x02\x00' + bytes(4) + b'\x02\x00\x06\x11')

    def test_identity_changes_outside_echo_fields_do_not_change_reply(self):
        profile = json.loads(PROFILE.read_text(encoding='utf-8'))
        template = bytes.fromhex(profile['templates']['response_06:51:0000']['payload_hex'])
        payload = template[:23] + bytes(range(28))
        self.assertEqual(identity_acknowledgement(payload, template), template)
        with self.assertRaises(ValueError):
            identity_acknowledgement(b'\xff' + payload[1:], template)


if __name__ == '__main__':
    unittest.main()
