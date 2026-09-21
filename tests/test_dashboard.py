import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from codex_dashboard.rpc import Rpc
from codex_dashboard.service import Service
from codex_dashboard.state import State, status_label
from codex_dashboard.ui import Dashboard, clean, crop, wrap
from codex_dashboard.worktrees import create_worktree, git

ROOT = Path(__file__).resolve().parents[1]


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cdx-test-")
        self.addCleanup(self.temp.cleanup)
        self.state = State(self.temp.name)

    def test_status_tracks_actual_waits_and_disconnect(self):
        s = self.state
        s.event("turn/started", {"threadId": "t", "turn": {"id": "u", "status": "inProgress"}})
        t = s.thread("t")
        self.assertEqual(status_label(t), "working")
        self.assertEqual(status_label(t, now=time.time() + 130), "quiet (active)")
        s.event("thread/status/changed", {"threadId": "t", "status": {"type": "active", "activeFlags": ["waitingOnApproval"]}})
        self.assertEqual(status_label(t), "approval")
        s.event("thread/status/changed", {"threadId": "t", "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]}})
        self.assertEqual(status_label(t), "question")
        self.assertEqual(status_label(t, connected=False), "offline")
        s.event("turn/completed", {"threadId": "t", "turn": {"id": "u", "status": "failed", "error": {"message": "test failure"}}})
        self.assertEqual(status_label(t), "error")
        self.assertIsNone(t["turnId"])

    def test_streamed_final_message_replaces_deltas_and_survives_restart(self):
        s = self.state
        for delta in ["Hello ", "world"]:
            s.event("item/agentMessage/delta", {"threadId": "t", "itemId": "i", "delta": delta})
        s.event("item/completed", {"threadId": "t", "item": {"id": "i", "type": "agentMessage", "text": "Hello world!"}})
        s.save()
        restored = State(self.temp.name)
        self.assertEqual(restored.thread("t")["items"], [{"id": "i", "type": "agentMessage", "text": "Hello world!"}])
        self.assertFalse(restored.connected)
        self.assertEqual(os.stat(restored.path).st_mode & 0o777, 0o600)

    def test_polling_metadata_does_not_discard_chat_or_plan(self):
        s = self.state
        s.event("item/agentMessage/delta", {"threadId": "t", "itemId": "i", "delta": "Keep me"})
        s.event("turn/plan/updated", {"threadId": "t", "plan": [{"step": "One", "status": "pending"}]})
        s.merge_thread({"id": "t", "turns": [], "status": {"type": "active", "activeFlags": []}})
        self.assertEqual(s.thread("t")["items"][0]["text"], "Keep me")
        self.assertEqual(len(s.thread("t")["plan"]), 1)

    def test_terminal_text_strips_control_sequences_and_wraps_wide_glyphs(self):
        self.assertNotIn("\x1b", clean("\x1b[31mhello"))
        self.assertEqual(clean("\x1b[31mhello\x1b[0m"), "hello")
        self.assertEqual(clean("\x1b]0;title\x07hello"), "hello")
        self.assertEqual(crop("猫猫abc", 5), "猫猫a")
        self.assertEqual(wrap("猫猫猫", 4), ["猫猫", "猫"])

    def test_draft_stays_with_its_selected_chat(self):
        ui = Dashboard(self.temp.name, {"threads": {}, "tasks": []})
        ui.switch("thread:a")
        ui.buffer = "message for a"
        ui.switch("thread:b")
        self.assertEqual(ui.buffer, "")
        ui.buffer = "message for b"
        ui.switch("thread:a")
        self.assertEqual(ui.buffer, "message for a")

    def test_paste_does_not_execute_embedded_commands(self):
        ui = Dashboard(self.temp.name, {"threads": {}, "tasks": []})
        for key in "\x1b[200~/quit\n/start\n\x1b[201~":
            ui.input_key(key)
        self.assertFalse(ui.stopped.is_set())
        self.assertEqual(ui.buffer, "/quit\n/start\n")
        self.assertFalse(ui.pasting)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cdx-test-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        await git("init", "-b", "main", str(self.repo))
        await git("-C", str(self.repo), "config", "user.email", "test@example.invalid")
        await git("-C", str(self.repo), "config", "user.name", "Dashboard Test")
        (self.repo / "tracked.txt").write_text("original")
        await git("-C", str(self.repo), "add", "tracked.txt")
        await git("-C", str(self.repo), "commit", "-m", "fixture")
        (self.repo / "tracked.txt").write_text("uncommitted work")
        self.service = Service(self.root / "state", [sys.executable, "-B", str(ROOT / "tests/fake_codex.py")])
        self.service.rpc = Rpc(self.service.command, self.service.state.event, self.service.on_request)
        await self.service.rpc.connect()
        self.service.state.models = await self.service.pages("model/list", {})
        self.service.state.connected = True

    async def asyncTearDown(self):
        await self.service.rpc.close()
        self.temp.cleanup()

    async def test_worktree_preserves_dirty_original_checkout(self):
        before = await git("-C", str(self.repo), "status", "--porcelain")
        info = await create_worktree(self.repo, self.root / "worktrees", "abcdef123456", "Test task")
        self.assertEqual(await git("-C", str(self.repo), "branch", "--show-current"), "main")
        self.assertEqual(await git("-C", str(self.repo), "status", "--porcelain"), before)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "uncommitted work")
        self.assertEqual((Path(info["cwd"]) / "tracked.txt").read_text(), "original")
        (Path(info["cwd"]) / "tracked.txt").write_text("agent work")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "uncommitted work")

    async def test_planning_is_inert_and_double_start_is_rejected(self):
        task = await self.service.dispatch({"action": "plan", "repo": str(self.repo), "title": "Example", "prompt": "Do work"})
        self.assertFalse((self.root / "state/worktrees").exists())
        restored = State(self.root / "state")
        self.assertEqual(restored.data["tasks"][0]["status"], "planned")
        first, second = await asyncio.gather(*[self.service.dispatch({"action": "start", "taskId": task["id"]}) for _ in range(2)], return_exceptions=True)
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, ValueError)
        self.assertEqual(len(self.service.state.data["threads"]), 1)
        await asyncio.sleep(1)
        self.assertEqual(task["status"], "completed")

    async def test_model_choice_steering_approval_and_background_completion(self):
        await self.service.dispatch({"action": "settings", "model": "test-model", "effort": "high"})
        with self.assertRaises(ValueError):
            await self.service.dispatch({"action": "settings", "model": "test-model", "effort": "invalid"})
        task = await self.service.dispatch({"action": "plan", "repo": str(self.repo), "title": "Background", "prompt": "Do work"})
        await self.service.dispatch({"action": "start", "taskId": task["id"]})
        tid = task["threadId"]
        t = self.service.state.thread(tid)
        self.assertEqual(t["nextEffort"], "high")
        result = await self.service.dispatch({"action": "send", "threadId": tid, "text": "Also check tests"})
        self.assertEqual(result["turnId"], t["turnId"])
        await self.service.rpc.call("test/approval", {"threadId": tid})
        self.assertEqual(len(self.service.state.requests), 1)
        self.assertEqual(status_label(t), "approval")
        # No UI client exists here: pending requests and streamed messages remain owned by the service.
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.service.state.requests), 1)
        await self.service.dispatch({"action": "respond", "requestId": "approval-1", "response": {"decision": "decline"}})
        await asyncio.sleep(1)
        self.assertEqual(status_label(t), "idle")
        self.assertEqual(t["items"][-1]["text"], "Background work completed")
        self.service.state.save()
        restored = State(self.root / "state")
        self.assertEqual(restored.thread(tid)["items"][-1]["text"], "Background work completed")
        self.assertEqual(restored.data["tasks"][0]["status"], "completed")

    async def test_planned_task_carries_setup_into_its_agent_and_worktree(self):
        self.service.state.data["projectProfiles"] = {str(self.repo.resolve()): {"config": {"devPort": "5173"}}}
        task = await self.service.dispatch({"action": "plan", "repo": str(self.repo), "title": "Configured task", "prompt": "Do work"})
        await self.service.dispatch({"action": "agent_setup", "scope": "agent", "target": "task:" + task["id"],
                                     "patch": {"model": "test-model", "effort": "high", "acceptance": "Keep keyboard access", "testPort": "5181"}})
        await self.service.dispatch({"action": "start", "taskId": task["id"]})
        t = self.service.state.thread(task["threadId"])
        from codex_dashboard.agent_setup import effective_config
        effective = effective_config(self.service.state.data, "thread:" + t["id"])
        self.assertEqual(effective["devPort"], "5173")
        self.assertEqual(effective["testPort"], "5181")
        self.assertEqual(effective["acceptance"], "Keep keyboard access")
        self.assertEqual(effective["effort"], "high")
        self.assertNotEqual(t["cwd"], str(self.repo))


if __name__ == "__main__":
    unittest.main()
