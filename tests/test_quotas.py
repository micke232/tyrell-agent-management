import asyncio
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from tyrell.quotas import codex_quota, copilot_quota, quota_label
from tyrell.connections import connection_badges, connections_text
from tyrell.rpc import RpcError
from tyrell.service import Service


class Quotas(unittest.TestCase):
    def test_codex_windows_and_buckets_do_not_double_count_legacy(self):
        window = {'usedPercent': 75, 'windowDurationMins': 300}
        result = codex_quota({'rateLimits': {'primary': window},
                             'rateLimitsByLimitId': {'codex': {'primary': window,
                             'secondary': {'usedPercent': 100, 'windowDurationMins': 10080}}}})
        self.assertEqual(quota_label({'quota': result}), '5h 25% left / 7d 0% left')
        self.assertEqual(codex_quota({'rateLimits': {'primary': {'usedPercent': float('nan')}}})['rows'], [])

    def test_copilot_unlimited_and_overage_are_distinct_from_remaining(self):
        quota = copilot_quota({'quotaSnapshots': {
            'premium_interactions': {'remainingPercentage': 0, 'usageAllowedWithExhaustedQuota': True},
            'chat': {'entitlementRequests': -1}}})
        self.assertIn('premium 0% left', quota_label({'quota': quota}))
        self.assertIn('chat Unlimited', quota_label({'quota': quota}, detail=True))
        self.assertIn('additional usage allowed', quota_label({'quota': quota}, detail=True))

    def test_missing_stale_expired_and_narrow_terminal(self):
        self.assertEqual(quota_label({}), 'Quota unavailable')
        info = {'status': 'connected', 'quota': {'checkedAt': time.time()-200,
                'rows': [{'label': '5h', 'remaining': 0, 'reset': time.time()-1}]}}
        self.assertEqual(quota_label(info), 'Stale · 5h 0% left (reset due)')
        data = {'providers': {'codex': info}}
        self.assertIn('reset due', connections_text(data))
        self.assertNotIn('reset due', ' '.join(x[0] for x in connection_badges(data, 500)))
        self.assertLessEqual(sum(len(x[0])+2 for x in connection_badges(data, 60)), 60)
        self.assertNotIn('reset due', ' '.join(x[0] for x in connection_badges(data, 500, demo=True)))


class QuotaMonitor(unittest.IsolatedAsyncioTestCase):
    async def test_optional_rpc_failure_keeps_connection_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory, [])
            service.stop = asyncio.Event()
            service.state.connected = True
            async def failed(*args, **kwargs):
                service.stop.set()
                raise RpcError('unsupported')
            service.rpc = type('Fake', (), {'call': AsyncMock(side_effect=failed)})()
            await service.monitor_quotas()
            self.assertTrue(service.state.connected)
            self.assertEqual(service.codex_quota, {})

    async def test_account_change_clears_quota_and_notification_requests_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory, [])
            service.codex_quota = {'rows': [{'remaining': 1}]}
            service.quota_due = time.monotonic()+60
            service.codex_event('account/updated', {})
            self.assertEqual(service.codex_quota, {})
            self.assertEqual(service.quota_due, 0)
