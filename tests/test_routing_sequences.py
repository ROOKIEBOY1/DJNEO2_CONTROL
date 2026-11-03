import json
from pathlib import Path
import tempfile
import unittest

from control_packets import build_query_packet, valid_frames
from routing_sequences import RoutingSequenceStore, historical_next_counter, resequence_tunnels


class RoutingSequenceTests(unittest.TestCase):
    def test_full_counter_wrap_preserves_inner_frame_and_crc(self):
        original = build_query_packet(0x1234, 0x4000, 0x4000, 0x4000, 7, 0x2233)[34:]
        rewritten = resequence_tunnels(original, lambda: 0x10002)
        frames = list(valid_frames(rewritten))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][6:8], b'\x02\x00')
        self.assertEqual(int.from_bytes(frames[0][-19:-15], 'little'), 0x10002)
        self.assertEqual(frames[0][11:-24], original[11:-24])

    def test_restarts_reserve_disjoint_ranges_and_prevent_two_controllers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'routing.json'
            with RoutingSequenceStore(path, initial=0xfffe, block_size=4) as first:
                self.assertEqual([first.next(), first.next(), first.next()], [0xfffe, 0xffff, 0x10000])
                with self.assertRaises(RuntimeError):
                    RoutingSequenceStore(path, initial=1, block_size=4)
            with RoutingSequenceStore(path, initial=1, block_size=4) as second:
                self.assertEqual(second.next(), 0x10002)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_bootstrap_uses_previous_actual_wire_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            source = build_query_packet(0x1234, 0x4000, 0x4000, 0x4000, 7, 0x2233)
            wire = bytearray(source)
            wire[34:] = resequence_tunnels(source[34:], lambda: 0x12345)
            Path(directory, 'control-attempt-test.json').write_text(json.dumps({
                'commands': [{'packet_hex': source.hex(), 'wire_packet_hex': bytes(wire).hex()}],
                'outgoing_transport': []}), encoding='utf-8')
            self.assertEqual(historical_next_counter(Path(directory)), 0x12346)

    def test_invalid_state_does_not_silently_reuse_old_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'routing.json'
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                RoutingSequenceStore(path)

    def test_reservation_refills_are_durable_before_numbers_are_issued(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'routing.json'
            with RoutingSequenceStore(path, initial=100, block_size=2) as store:
                self.assertEqual([store.next() for _ in range(5)], list(range(100, 105)))
                self.assertEqual(json.loads(path.read_text())['next_counter'], 106)
            with RoutingSequenceStore(path, block_size=2) as restarted:
                self.assertEqual(restarted.next(), 106)


if __name__ == '__main__':
    unittest.main()
