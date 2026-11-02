"""Offline/local-simulation test suite, with explicit private-fixture skips."""
from pathlib import Path
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / '开发基地1' / 'tools'
CAPTURES = TOOLS.parent / 'captures'
sys.path.insert(0, str(TOOLS))

# Preserve source tests unchanged; only this portable runner marks absent data.
REQUIRED = {
    'test_control_packets.ControlPacketTests.test_actual_successful_takeoff_and_landing_replies_are_recognized': ['control-attempt-20261029-112328.json'],
    'test_registration_exchange.RegistrationTests.test_changed_live_capability_and_identity_fields_receive_correct_responses': ['control-attempt-20261029-110016.json'],
    'test_registration_exchange.RegistrationTests.test_responses_match_a_second_recorded_connection': ['dji-fly-20261027-1949.pcapng'],
    'test_registration_schema.RegistrationSchemaTests.test_channel_lists_reproduce_recorded_phone_acknowledgements': ['dji-fly-20261027-1949.pcapng'],
    'test_reliable_console.ReliableConsoleTests.test_registration_answers_current_peer_sequence_over_reliable_data': ['cold-all-retry-20261027.pcapng'],
    'test_media_directory.DirectoryTests': ['camera-media-20261030-141959.pcapng'],
    'test_media_directory_link.DirectoryStreamTests.test_real_seven_packet_response_reorders_to_latest_originals': ['camera-media-20261030-141959.pcapng'],
}

def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item

def main():
    suite = unittest.defaultTestLoader.discover(str(TOOLS), pattern='test_*.py')
    for case in cases(suite):
        reason = None
        if case.id().startswith('test_capture_media_session.') and importlib.util.find_spec('pymobiledevice3') is None:
            reason = 'Optional dependency pymobiledevice3 is not installed'
        if 'test_context_is_present_before_photo_mode_in_both_successful_phone_captures' in case.id():
            if not all((CAPTURES / f'camera-media-20261030-{s}.duml.jsonl').exists() for s in ('141959', '164926')):
                reason = 'Original camera capture fixtures are not distributed'
        for key, files in REQUIRED.items():
            if case.id() == key or case.id().startswith(key + '.'):
                missing = [name for name in files if not (CAPTURES / name).exists()]
                if missing:
                    reason = 'Private capture fixture not distributed: ' + ', '.join(missing)
                    if key == 'test_media_directory.DirectoryTests':
                        case.__class__.__unittest_skip__ = True
                        case.__class__.__unittest_skip_why__ = reason
        if reason:
            method = getattr(case.__class__, case._testMethodName)
            setattr(case.__class__, case._testMethodName, unittest.skip(reason)(method))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
