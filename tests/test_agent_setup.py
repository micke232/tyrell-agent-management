import curses
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tyrell.agent_setup import (DEFAULTS, FIELDS, discover_project, effective_config,
                                         instruction_text, validate_patch)
from tyrell.service import Service
from tyrell.state import State
from tyrell.ui import Dashboard
from test_presentation import Screen


MODELS = [{"model": "test-model", "displayName": "Test", "isDefault": True,
           "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}],
           "serviceTiers": [{"id": "priority", "name": "Fast", "description": "Increased usage"}]}]


class SetupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(self.temp.name, [])
        self.service.state.models = MODELS
        self.service.state.connected = True
        self.thread = self.service.state.thread("a")
        self.thread.update(cwd=self.temp.name, model="test-model", status={"type": "idle"})
        self.service.subscribed.add("a")
        self.service.rpc = SimpleNamespace(call=AsyncMock(return_value={"turn": {"id": "t", "status": "inProgress"}}))

    async def save(self, **kwargs):
        return await self.service.dispatch({"action": "agent_setup", "scope": "agent", "target": "thread:a", **kwargs})

    async def test_settings_persist_without_running_commands_and_reject_invalid_values_atomically(self):
        await self.save(patch={"processes": "Own isolated server", "testPort": "5181", "terraformPlan": True})
        self.service.rpc.call.assert_not_called()
        self.assertEqual(State(self.temp.name).thread("a")["agentConfig"]["testPort"], "5181")
        previous = deepcopy(self.thread["agentConfig"])
        for bad in ({"testPort": "70000"}, {"e2e": "yes"}, {"model": "not-in-catalog"},
                    {"effort": "ultra"}, {"tier": "fake-fast"}, {"unexpected": True}, {"testPort": "１２"},
                    {"baseUrl": "https://user:password@localhost:5173"}):
            with self.assertRaises(ValueError):
                await self.save(patch=bad)
            self.assertEqual(self.thread["agentConfig"], previous)

    async def test_model_and_work_preferences_reach_the_turn_without_changing_user_text(self):
        await self.save(patch={"model": "test-model", "effort": "high", "tier": "priority",
                               "acceptance": "Keyboard navigation works", "testPort": "5181"})
        await self.service.send_message("a", "Please fix it", "client-1")
        method, params = self.service.rpc.call.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertEqual((params["model"], params["effort"], params["serviceTier"]), ("test-model", "high", "priority"))
        self.assertEqual(params["input"][0]["text"], "Please fix it")
        self.assertEqual(params["clientUserMessageId"], "client-1")
        context = params["additionalContext"]["dashboard-setup"]["value"]
        self.assertIn("Keyboard navigation works", context)
        self.assertIn("5181", context)
        self.assertIn("dashboard-progress", params["additionalContext"])

    async def test_mid_turn_preferences_steer_without_attempting_to_replace_model(self):
        self.thread.update(status={"type": "active"}, turnId="running")
        await self.save(patch={"model": "test-model", "effort": "high", "tier": "priority", "verbosity": "Detailed"})
        await self.service.send_message("a", "One more thing")
        method, params = self.service.rpc.call.call_args.args
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "running")
        self.assertFalse({"model", "effort", "serviceTier"} & params.keys())
        self.assertIn("Detailed", params["additionalContext"]["dashboard-setup"]["value"])

    async def test_agent_project_defaults_inheritance_reset_and_task_worktree(self):
        await self.service.dispatch({"action": "agent_setup", "scope": "defaults", "patch": {"verbosity": "Detailed", "unit": False}})
        self.service.state.data["projectProfiles"] = {str(Path(self.temp.name).resolve()): {"config": {"verbosity": "Balanced", "devPort": "5173"}}}
        await self.save(patch={"verbosity": "Concise"})
        data = self.service.state.data
        config = effective_config(data, "thread:a")
        self.assertEqual((config["verbosity"], config["unit"], config["devPort"]), ("Concise", False, "5173"))
        await self.save(reset=True)
        self.assertEqual(effective_config(data, "thread:a")["verbosity"], "Balanced")
        self.thread.update(projectRoot=self.temp.name, cwd="/separate/worktree")
        self.assertEqual(effective_config(data, "thread:a")["devPort"], "5173")
        data["tasks"].append({"id": "planned", "repo": self.temp.name})
        await self.service.dispatch({"action": "agent_setup", "scope": "agent", "target": "task:planned", "patch": {"acceptance": "No regressions"}})
        self.assertEqual(effective_config(data, "task:planned")["acceptance"], "No regressions")

    async def test_standard_speed_explicitly_clears_the_override(self):
        await self.save(patch={"tier": "default"})
        await self.service.send_message("a", "hello")
        self.assertIn("serviceTier", self.service.rpc.call.call_args.args[1])
        self.assertIsNone(self.service.rpc.call.call_args.args[1]["serviceTier"])

    async def test_unknown_targets_are_not_created_by_settings(self):
        for args in ({"scope": "agent", "target": "thread:unknown"}, {"scope": "project", "path": "/missing"}):
            with self.assertRaises(ValueError):
                await self.service.dispatch({"action": "agent_setup", "patch": {"verbosity": "Detailed"}, **args})
        self.assertNotIn("unknown", self.service.state.data["threads"])

    async def test_form_service_round_trip_and_restart_keep_scopes_separate(self):
        snapshot = await self.service.dispatch({"action": "snapshot", "threadId": "a"})
        ui = Dashboard(self.temp.name, snapshot)
        ui.demo = False
        ui.update()
        ui.commands.get_nowait()  # Initial thread selection.
        ui.view, ui.focus = "setup", "history"
        ui.setup.expanded.add("Runtime & ports")
        ui.setup.build_rows(ui)
        index = next(i for i, r in enumerate(ui.setup.rows) if r["action"] == "cleanup")
        ui.setup.activate(ui, index)
        action, params = ui.commands.get_nowait()
        result = await self.service.dispatch({"action": action, **params})
        ui.updates.put(("done", (action, result, params)))
        ui.update()
        self.assertFalse(ui.setup.context(ui)[2]["cleanup"])
        restored = State(self.temp.name)
        self.assertFalse(restored.thread("a")["agentConfig"]["cleanup"])
        self.assertNotIn("agentConfig", restored.data["settings"])
        self.service.rpc.call.assert_not_called()

    async def test_project_import_preserves_manual_edits_and_global_defaults(self):
        (Path(self.temp.name) / "package.json").write_text(json.dumps({"name": "fixture", "scripts": {"test:unit": "vitest run"}}))
        first = await self.service.dispatch({"action": "setup_import", "path": self.temp.name})
        path = first["profile"]["path"]
        await self.service.dispatch({"action": "agent_setup", "scope": "project", "path": path, "patch": {"unitCommand": "npm run test:unit -- custom"}})
        second = await self.service.dispatch({"action": "setup_import", "path": self.temp.name})
        self.assertEqual(second["profile"]["config"]["unitCommand"], "npm run test:unit -- custom")
        self.assertFalse(self.service.state.data["settings"])
        self.service.rpc.call.assert_not_called()


class SetupDiscoveryTests(unittest.TestCase):
    def test_project_discovery_is_generic_does_not_read_credentials_or_execute_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "different-project", "dependencies": {"react": "19", "vite": "8"}, "scripts": {"dev": "touch SHOULD_NOT_EXIST", "test:unit": "exit 1"}}))
            (root / ".nvmrc").write_text("v24.0.0")
            (root / ".env.local").write_text("SECRET=never-copy-this")
            (root / ".npmrc").write_text("TOKEN=never-copy-this")
            (root / "README.md").write_text("Run https://localhost:5199/. Docs https://git.example/team/repo/wiki")
            result = discover_project(root)
            self.assertEqual(result["name"], "different-project")
            self.assertEqual(result["config"]["devPort"], "5199")
            self.assertEqual(result["config"]["unitCommand"], "npm run test:unit")
            self.assertNotIn("never-copy-this", json.dumps(result))
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())
            self.assertNotIn("cs-client", json.dumps(result))

    def test_plan_only_and_terraform_do_not_authorize_implementation_or_apply(self):
        text = instruction_text({**DEFAULTS, "mode": "Plan only", "terraformPlan": True})
        self.assertIn("do not modify files", text)
        self.assertIn("Do not execute test/build/dev commands", text)
        self.assertIn("Never run Terraform apply, destroy", text)
        self.assertNotIn("cs-client", text)


class SetupUITests(unittest.TestCase):
    def make_ui(self):
        ui = Dashboard("/tmp", {"connected": True, "threads": {"a": {"id": "a", "name": "Agent", "model": "test-model", "status": {"type": "idle"}}}, "models": MODELS})
        ui.update()
        ui.view, ui.focus = "setup", "history"
        ui.buffer, ui.cursor = "unsent chat draft", 4
        return ui

    def test_keyboard_checkbox_choice_and_text_edits_preserve_chat_draft(self):
        ui = self.make_ui()
        ui.setup.expanded.update(("Communication", "Task brief"))
        with patch("curses.curs_set"), patch.object(ui, "submit") as submit:
            ui.render(Screen(38, 120))
            ui.setup.index = next(i for i, r in enumerate(ui.setup.rows) if r["action"] == "evidence")
            ui.key(" ")
            submit.assert_called_with("agent_setup", scope="agent", target="thread:a", patch={"evidence": False}, reset=False)
            ui.setup.index = next(i for i, r in enumerate(ui.setup.rows) if r["action"] == "objective")
            ui.key("\r")
            ui.buffer = "Improve the forms"
            ui.entered()
            submit.assert_called_with("agent_setup", scope="agent", target="thread:a", patch={"objective": "Improve the forms"}, reset=False)
            self.assertEqual(ui.buffer, "unsent chat draft")
            self.assertEqual(ui.focus, "history")
            ui.setup.activate(ui, next(i for i, r in enumerate(ui.setup.rows) if r["action"] == "objective"))
            ui.buffer = "cancel this"
            ui.key("\x1b")
            self.assertEqual((ui.buffer, ui.view), ("unsent chat draft", "chat"))

    def test_clickable_groups_fields_preview_and_small_terminal(self):
        ui = self.make_ui()
        with patch("curses.curs_set"), patch.object(ui, "submit") as submit:
            screen = Screen(38, 120)
            ui.render(screen)
            index = next(i for i, row in enumerate(ui.setup.rows) if row["action"] == "section:Infra")
            y = next(y for y, value in ui.setup.hits.items() if value == index)
            ui.mouse(0, ui.history_bounds[0] + 2, y)
            self.assertIn("Infra", ui.setup.expanded)
            ui.setup.activate(ui, next(i for i, row in enumerate(ui.setup.rows) if row["action"] == "preview"))
            self.assertIn("AGENT SETUP PREVIEW", ui.panel)
            submit.assert_not_called()
            ui.panel = None
            ui.render(Screen(24, 70))
            self.assertTrue(any(title == "setup" for _, _, title in ui.hit_tabs))
            self.assertLessEqual(ui.hit_tabs[-1][1], 68)

    def test_saved_config_survives_an_older_poll_and_a_failed_save_does_not_apply(self):
        ui = self.make_ui()
        ui.setup.saved({"scope": "agent", "target": "thread:a", "path": None,
                        "config": {"verbosity": "Detailed"}, "revision": 2})
        self.assertEqual(ui.setup.context(ui)[2]["verbosity"], "Detailed")
        ui.data["setupRevision"] = 1
        self.assertEqual(ui.setup.context(ui)[2]["verbosity"], "Detailed")
        ui.data["threads"]["a"]["agentConfig"] = {"verbosity": "Balanced"}
        ui.data["setupRevision"] = 3
        self.assertEqual(ui.setup.context(ui)[2]["verbosity"], "Balanced")
        ui.setup.pending = True
        ui.updates.put(("error", ("Disconnected", "agent_setup", {"patch": {"verbosity": "Concise"}})))
        ui.update()
        self.assertFalse(ui.setup.pending)
        self.assertEqual(ui.setup.context(ui)[2]["verbosity"], "Balanced")

    def test_empty_value_clears_field_and_invalid_port_stays_editable(self):
        ui = self.make_ui()
        ui.setup.expanded.add("Runtime & ports")
        ui.setup.build_rows(ui)
        index = next(i for i, r in enumerate(ui.setup.rows) if r["action"] == "testPort")
        with patch.object(ui, "submit") as submit:
            ui.setup.activate(ui, index)
            ui.buffer = "70000"
            ui.entered()
            submit.assert_not_called()
            self.assertIsNotNone(ui.wizard)
            ui.buffer = ""
            ui.entered()
            submit.assert_called_with("agent_setup", scope="agent", target="thread:a", patch={"testPort": ""}, reset=False)
