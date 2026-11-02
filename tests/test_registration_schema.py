import json
import unittest

from messages import duml_messages
from registration_exchange import PROFILE
from registration_schema import channel_acknowledgement, channel_offer, identity_acknowledgement


class RegistrationSchemaTests(unittest.TestCase):

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


