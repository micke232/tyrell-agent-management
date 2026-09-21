import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from codex_dashboard.rpc import Rpc
from codex_dashboard.service import Service
from codex_dashboard.state import State

ROOT = Path(__file__).resolve().parents[1]


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cdx-archive-")
        self.service = Service(self.temp.name, [sys.executable, "-B", str(ROOT / "tests/fake_codex.py")])
        self.service.rpc = Rpc(self.service.command, self.service.state.event, self.service.on_request)
        await self.service.rpc.connect()
        self.service.state.connected = True
        result = await self.service.rpc.call("thread/start", {"cwd": self.temp.name})
        self.tid = result["thread"]["id"]

    async def asyncTearDown(self):
        await self.service.rpc.close()
        self.temp.cleanup()

    async def wait_for_completion(self):
        async def completed():
            while self.service.state.thread(self.tid)["lastTurnStatus"] != "completed":
                await asyncio.sleep(.01)
        await asyncio.wait_for(completed(), timeout=5)

    async def test_remove_is_persistent_and_does_not_archive_or_interrupt(self):
        await self.service.dispatch({"action": "send", "threadId": self.tid, "text": "work"})
        await self.service.dispatch({"action": "remove", "threadId": self.tid})
        await self.service.refresh()
        s = await self.service.dispatch({"action": "snapshot"})
        self.assertNotIn(self.tid, s["threads"])
        self.assertIn(self.tid, s["hiddenThreads"])
        self.assertEqual(State(self.temp.name).data["hidden"], [self.tid])
        await self.wait_for_completion()
        self.assertEqual(self.service.state.thread(self.tid)["lastTurnStatus"], "completed")
        await self.service.dispatch({"action": "restore", "threadId": self.tid})
        self.assertIn(self.tid, (await self.service.dispatch({"action": "snapshot"}))["threads"])

    async def test_archive_browse_restore_preserves_history(self):
        await self.service.dispatch({"action": "send", "threadId": self.tid, "text": "work"})
        await self.wait_for_completion()
        await self.service.dispatch({"action": "archive", "threadId": self.tid})
        await self.service.refresh()
        self.assertNotIn(self.tid, self.service.state.data["threads"])
        archives = await self.service.dispatch({"action": "archives"})
        self.assertIn(self.tid, archives["archived"])
        t = await self.service.dispatch({"action": "read_archive", "threadId": self.tid})
        self.assertEqual(t["items"][-1]["text"], "Background work completed")
        with self.assertRaisesRegex(ValueError, "archived"):
            await self.service.dispatch({"action": "send", "threadId": self.tid, "text": "do not start"})
        await self.service.dispatch({"action": "restore", "threadId": self.tid})
        self.assertIn(self.tid, self.service.state.data["threads"])
        self.assertNotIn(self.tid, self.service.state.data["archived"])
        self.assertEqual(self.service.state.thread(self.tid)["items"][-1]["text"], "Background work completed")

    async def test_active_agent_cannot_be_archived(self):
        await self.service.dispatch({"action": "send", "threadId": self.tid, "text": "work"})
        with self.assertRaisesRegex(ValueError, "interrupt"):
            await self.service.dispatch({"action": "archive", "threadId": self.tid})
        self.assertIn(self.tid, self.service.state.data["threads"])
        self.assertFalse(self.service.state.data["archived"])

    async def test_message_identity_survives_send_steer_and_reload(self):
        for client_id in ("first-message", "second-message"):
            await self.service.dispatch({"action": "send", "threadId": self.tid, "text": "same text", "clientId": client_id})
        await self.service.subscribe(self.tid)
        messages = [item for item in self.service.state.thread(self.tid)["items"] if item["type"] == "userMessage"]
        self.assertEqual([item["clientId"] for item in messages], ["first-message", "second-message"])

    async def test_rename_survives_refresh_restart_and_archive(self):
        await self.service.dispatch({"action": "rename", "threadId": self.tid, "name": "Min agent"})
        await self.service.refresh()
        self.assertEqual(self.service.state.thread(self.tid)["name"], "Min agent")
        self.assertEqual(State(self.temp.name).thread(self.tid)["name"], "Min agent")
        await self.service.dispatch({"action": "archive", "threadId": self.tid})
        await self.service.dispatch({"action": "rename", "threadId": self.tid, "name": "Arkiverad agent"})
        await self.service.dispatch({"action": "restore", "threadId": self.tid})
        self.assertEqual(self.service.state.thread(self.tid)["name"], "Arkiverad agent")
        for invalid in (" ", "two\nlines", "x" * 101):
            with self.assertRaises(ValueError):
                await self.service.dispatch({"action": "rename", "threadId": self.tid, "name": invalid})
