import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from codex_dashboard.connections import CopilotConnection, CopilotProbe, normalize_host
from codex_dashboard.service import Service
from codex_dashboard.ui import Dashboard, cells
from test_presentation import Screen


class ConnectionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_status_stays_separate_and_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory, [])
            service.copilot.update("connected", models=[{"id": "copilot-model", "name": "Copilot model"}])
            snapshot = await service.dispatch({"action": "snapshot"})
            self.assertFalse(snapshot["providers"]["codex"]["connected"])
            self.assertTrue(snapshot["providers"]["copilot"]["connected"])
            self.assertEqual(snapshot["models"], [])
            await service.dispatch({"action": "copilot_host", "host": "company.ghe.com"})
            data = json.loads((Path(directory) / "state.json").read_text())
            self.assertEqual(data["settings"]["copilotHost"], "https://company.ghe.com")
            self.assertNotIn("providers", data)
            self.assertNotIn("copilot-model", json.dumps(data))

    async def test_framing_handles_fragments_notifications_and_rejects_work(self):
        probe = CopilotProbe()
        reader = asyncio.StreamReader()
        writer = SimpleNamespace(write=lambda value: None, drain=AsyncMock())
        probe.process = SimpleNamespace(stdout=reader, stdin=writer)
        for message in ({"jsonrpc": "2.0", "method": "notification", "params": {}},
                        {"jsonrpc": "2.0", "id": 1, "result": {"isAuthenticated": False}}):
            payload = json.dumps(message).encode()
            for part in (("Content-Length: %d\r\n\r\n" % len(payload)).encode(), payload[:5], payload[5:]):
                reader.feed_data(part)
        self.assertEqual(await probe.call("auth.getStatus"), {"isAuthenticated": False})
        with self.assertRaises(ValueError):
            await probe.call("session.create")

    async def test_probe_errors_do_not_expose_server_messages(self):
        probe = CopilotProbe()
        reader = asyncio.StreamReader()
        payload = json.dumps({"id": 1, "error": {"message": "sensitive-test-fixture", "code": 403}}).encode()
        reader.feed_data(("Content-Length: %d\r\n\r\n" % len(payload)).encode() + payload)
        probe.process = SimpleNamespace(stdout=reader, stdin=SimpleNamespace(write=lambda value: None, drain=AsyncMock()))
        with self.assertRaisesRegex(RuntimeError, "^Connection request failed$"):
            await probe.call("models.list")

    async def test_monitor_handles_login_account_access_and_cleans_up(self):
        for auth, models, expected in (
            ({"isAuthenticated": False}, {}, "signin"),
            ({"isAuthenticated": True, "host": "https://github.com"}, {}, "account"),
            ({"isAuthenticated": True, "host": "https://company.ghe.com"}, {}, "unavailable"),
            ({"isAuthenticated": True, "host": "https://company.ghe.com"}, {"models": [{"id": "m", "name": "Model"}]}, "connected"),
        ):
            monitor = CopilotConnection()
            stop = asyncio.Event()
            async def call(method):
                return {"ping": {"protocolVersion": 3}, "auth.getStatus": auth, "models.list": models}[method]
            with patch("codex_dashboard.connections.copilot_executable", return_value="copilot"), \
                 patch.object(CopilotProbe, "start", new_callable=AsyncMock), \
                 patch.object(CopilotProbe, "call", side_effect=call), \
                 patch.object(CopilotProbe, "close", new_callable=AsyncMock) as close:
                job = asyncio.create_task(monitor.run(stop, "/tmp", lambda: "https://company.ghe.com"))
                try:
                    for _ in range(20):
                        await asyncio.sleep(0)
                        if monitor.status["status"] != "connecting":
                            break
                    self.assertEqual(monitor.status["status"], expected)
                finally:
                    stop.set()
                    await asyncio.wait_for(job, 1)
                close.assert_awaited_once()

    async def test_timeout_changes_connected_status_and_closes_child(self):
        monitor = CopilotConnection()
        monitor.update("connected", models=[{"id": "m"}])
        stop = asyncio.Event()
        async def timeout(method):
            stop.set()
            raise asyncio.TimeoutError()
        with patch("codex_dashboard.connections.copilot_executable", return_value="copilot"), \
             patch.object(CopilotProbe, "start", new_callable=AsyncMock), \
             patch.object(CopilotProbe, "call", side_effect=timeout), \
             patch.object(CopilotProbe, "close", new_callable=AsyncMock) as close:
            await monitor.run(stop, "/tmp")
        self.assertEqual(monitor.status["status"], "offline")
        self.assertEqual(monitor.status["models"], [])
        close.assert_awaited_once()

    def test_enterprise_host_validation(self):
        self.assertEqual(normalize_host("COMPANY.GHE.COM/"), "https://company.ghe.com")
        for host in ("https://user:pass@example.com", "http://example.com", "https://example.com?token=x", "https://example.com/path"):
            with self.assertRaises(ValueError):
                normalize_host(host)


class ConnectionUIChecks(unittest.TestCase):
    def make_ui(self):
        ui = Dashboard("/tmp", {"connected": True, "threads": {}, "providers": {
            "codex": {"status": "connected", "models": []},
            "copilot": {"status": "connected", "models": [{"id": "m", "name": "Model"}], "host": "https://company.ghe.com"}}})
        ui.demo = False
        ui.buffer, ui.cursor = "keep draft", 4
        return ui

    def test_header_fits_and_connection_details_preserve_draft(self):
        ui = self.make_ui()
        with patch("curses.curs_set"):
            for width in (70, 90, 160):
                for state in ("connected", "signin", "missing", "connecting", "unavailable", "account", "offline"):
                    ui.data["providers"]["copilot"]["status"] = state
                    screen = Screen(24, width)
                    ui.render(screen)
                    title = next((x, text) for y, x, text, _ in screen.positions if y == 0 and "Tyrell Agent" in text)
                    self.assertIn("Tyrell Agent Management", title[1])
                    if width == 160:
                        self.assertIn("Tyrell Agent Management - They work. You take the credit.", title[1])
                    self.assertLessEqual(title[0] + cells(title[1]), ui.hit_connections[0][0])
                    self.assertLess(ui.hit_connections[-1][1], width)
            ui.mouse(0, ui.hit_connections[-1][0], 0)
            self.assertEqual(ui.panel, "CONNECTIONS")
            screen = Screen(38, 120)
            ui.render(screen)
            self.assertIn("https://company.ghe.com", "\n".join(screen.writes))
            ui.key("\x1b")
        self.assertEqual((ui.buffer, ui.cursor), ("keep draft", 4))

    def test_service_disconnect_clears_both_provider_badges(self):
        ui = self.make_ui()
        ui.updates.put(("offline", "Socket disconnected"))
        ui.update()
        self.assertEqual([p["status"] for p in ui.data["providers"].values()], ["offline", "offline"])
        self.assertEqual([p["models"] for p in ui.data["providers"].values()], [[], []])
