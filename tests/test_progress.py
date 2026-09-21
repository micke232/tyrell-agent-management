import tempfile
import unittest

from codex_dashboard.progress import estimate_label, message_plan, parse_estimate
from codex_dashboard.state import State


class ProgressTests(unittest.TestCase):
    def test_single_step_checklist_and_completion_without_plan_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(directory)
            state.event("turn/started", {"threadId": "t", "turn": {"id": "u"}})
            state.event("item/completed", {"threadId": "t", "item": {"id": "p", "type": "agentMessage", "text": "Plan:\n- [>] Gör ändringen\nETA: 1-2 min"}})
            self.assertEqual(state.thread("t")["plan"], [{"step": "Gör ändringen", "status": "inProgress"}])
            self.assertEqual(state.thread("t")["estimate"]["high"], 2)
            state.event("item/completed", {"threadId": "t", "item": {"id": "done", "type": "agentMessage", "text": "Plan:\n- [x] Gör ändringen"}})
            self.assertEqual(state.thread("t")["plan"][0]["status"], "completed")
            state.event("turn/plan/updated", {"threadId": "t", "turnId": "u", "plan": [{"step": "Native plan", "status": "pending"}]})
            state.event("item/completed", {"threadId": "t", "item": {"id": "p2", "type": "agentMessage", "text": "Plan:\n- [x] Old checklist"}})
            self.assertEqual(state.thread("t")["plan"][0]["step"], "Native plan")
            self.assertIsNone(message_plan("Example:\nPlan:\n- [x] Not a published plan"))

    def test_history_restores_latest_plan_without_resetting_estimate_age(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(directory)
            state.merge_thread({"id": "t", "turns": [
                {"id": "old", "status": "completed", "items": [{"id": "old-plan", "type": "agentMessage", "text": "Plan:\n- [x] Old task"}]},
                {"id": "latest", "status": "inProgress", "items": [{"id": "latest-plan", "type": "agentMessage", "text": "Plan:\n- [ ] New task\nETA: 1-2 min"}]}
            ]}, history=True)
            t = state.thread("t")
            self.assertEqual(t["plan"][0]["step"], "New task")
            self.assertEqual(t["planTurnId"], "latest")
            self.assertIsNone(t["estimate"])

    def test_only_explicit_estimates_display_with_age_and_expire(self):
        for text in (None, "probably 5 min", "ETA: 10-2 min", "ETA: 0-0 min"):
            self.assertIsNone(parse_estimate(text))
        t = {"estimate": parse_estimate("Tests remain.\nETA: 5-10 min", now=100)}
        self.assertIn("5–10 min", estimate_label(t, "working", now=220))
        self.assertIn("2 min ago", estimate_label(t, "working", now=220))
        self.assertIn("needs updating", estimate_label(t, "working", now=701))
        self.assertNotIn("min", estimate_label(t, "approval", now=220))
        self.assertEqual(estimate_label(t, "idle"), "")
        self.assertIn("not estimated", estimate_label({}, "working"))

    def test_plan_estimates_persist_but_do_not_leak_between_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(directory)
            event = {"threadId": "t", "turnId": "turn1", "plan": [{"step": "Test", "status": "inProgress"}], "explanation": "ETA: 1-3 min"}
            state.event("turn/plan/updated", event)
            state.save()
            restored = State(directory)
            self.assertEqual(restored.thread("t")["estimate"]["high"], 3)
            restored.merge_thread({"id": "t", "status": {"type": "active"}, "turns": [{"id": "turn1", "status": "inProgress"}]}, history=True)
            self.assertEqual(len(restored.thread("t")["plan"]), 1)
            restored.merge_thread({"id": "t", "turns": [{"id": "turn2", "status": "inProgress"}]}, history=True)
            self.assertFalse(restored.thread("t")["plan"])
            self.assertIsNone(restored.thread("t")["estimate"])
            state.event("turn/plan/updated", {**event, "explanation": None})
            self.assertIsNone(state.thread("t")["estimate"])
            state.event("turn/plan/updated", event)
            state.event("turn/started", {"threadId": "t", "turn": {"id": "turn3"}})
            self.assertFalse(state.thread("t")["plan"])
            self.assertIsNone(state.thread("t")["estimate"])
