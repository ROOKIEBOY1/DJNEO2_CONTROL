import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from capture_media_session import capture_session, validate_limits


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_closes_capture_and_preserves_packets_from_other_ports(self):
        from pymobiledevice3.services.pcapd import PcapdService
        from pcapng import FileScanner, blocks
        packet = SimpleNamespace(seconds=1700000000, microseconds=0, pid=1, comm='DJI Fly',
            epid=1, ecomm='DJI Fly', svc=0, data=b'\x00'*12+b'\x08\x00'+b'non-9003-media')
        class Service:
            def __init__(self, lockdown): self.lockdown = lockdown
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def watch(self):
                yield packet
                await asyncio.sleep(10)
            write_to_pcap = PcapdService.write_to_pcap
        link = SimpleNamespace(product_version='test', close=AsyncMock())
        factory = AsyncMock(return_value=link)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'media.pcapng'
            result = await capture_session(output, seconds=.03, max_bytes=100000,
                connect=factory, service_class=Service)
            self.assertEqual(result['reason'], 'timeout')
            self.assertEqual(result['packets'], 1)
            with output.open('rb') as source:
                frames = [b.packet_data for b in FileScanner(source) if isinstance(b, blocks.EnhancedPacket)]
            self.assertEqual(frames, [packet.data])
            self.assertTrue(output.with_suffix('.capture.json').exists())
        link.close.assert_awaited_once()

    async def test_existing_capture_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'keep.pcapng'
            output.write_bytes(b'keep')
            connect = AsyncMock()
            with self.assertRaises(FileExistsError):
                await capture_session(output, connect=connect)
            connect.assert_not_awaited()
            self.assertEqual(output.read_bytes(), b'keep')

    async def test_stop_file_ends_idle_capture(self):
        class Service:
            def __init__(self, lockdown): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def watch(self):
                await asyncio.sleep(10)
                if False: yield None
            async def write_to_pcap(self, out, packets):
                async for _ in packets: pass
        link = SimpleNamespace(close=AsyncMock())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'idle.pcapng'
            stop = output.with_suffix('.stop')
            stop.write_text('stop')
            result = await capture_session(output, seconds=5, max_bytes=100000,
                connect=AsyncMock(return_value=link), service_class=Service)
            self.assertEqual(result['reason'], 'stop_file')
        link.close.assert_awaited_once()


class LimitsTests(unittest.TestCase):
    def test_capture_limits_reject_unbounded_values(self):
        for seconds, size in ((0,10), (float('nan'),10), (601,10), (30,0), (30,2**31)):
            with self.assertRaises(ValueError): validate_limits(seconds,size)
