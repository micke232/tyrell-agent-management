import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tyrell.service import Service
from tyrell.hub_settings import settings_text


class ServiceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = Service(self.directory.name, ['fixture'])
        self.service.stop = asyncio.Event()

    async def test_failed_loop_restarts_without_exposing_exception_contents(self):
        service = self.service
        service.state.connected = True
        calls = []
        async def loop():
            calls.append(1)
            if len(calls) == 1:
                raise AttributeError('secret fixture value')
            service.stop.set()
        with patch('sys.stderr') as stderr:
            await asyncio.wait_for(service.supervise('Codex connection', loop), 5)
        self.assertEqual(len(calls), 2)
        self.assertFalse(service.state.connected)
        self.assertNotIn('secret fixture value', str(stderr.write.call_args_list))
        snapshot = await service.dispatch({'action': 'snapshot'})
        self.assertIn('Codex connection', snapshot['backgroundErrors'])
        self.assertIn('Background recovery', settings_text(snapshot, self.directory.name))

    async def test_shutdown_interrupts_retry_delay(self):
        service = self.service
        async def loop():
            service.stop.set()
            raise OSError('fixture')
        with patch('sys.stderr'):
            await asyncio.wait_for(service.supervise('Files', loop), .5)

    async def test_cancellation_does_not_restart_task(self):
        service = self.service
        loop = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await service.supervise('Files', loop)
        loop.assert_awaited_once()
        self.assertEqual(service.background_errors, {})

    async def test_one_time_check_finishes_without_retry(self):
        loop = AsyncMock()
        await self.service.supervise('CLI detection', loop, once=True)
        loop.assert_awaited_once()
        self.assertEqual(self.service.background_errors, {})
