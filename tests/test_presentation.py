import curses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tyrell.presentation import STATUS, PALETTE, activity_indicator, status_tone, syntax_spans, timeline, viewport
from tyrell.ui import Dashboard, crop, wrap


class Screen:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.writes = []
        self.positions = []

    def getmaxyx(self):
        return self.height, self.width

    def bkgd(self, *args):
        pass

    def erase(self):
        self.writes = []
        self.positions = []

    def addstr(self, y, x, text, style):
        assert 0 <= y < self.height and 0 <= x < self.width
        self.writes.append(text)
        self.positions.append((y, x, text, style))

    def move(self, y, x):
        assert 0 <= y < self.height and 0 <= x < self.width

    def refresh(self):
        pass


class PresentationTests(unittest.TestCase):
    def test_hover_changes_pointer_without_selecting_typing_or_copying(self):
        demo = json.loads((Path(__file__).resolve().parents[1] / "demo.json").read_text())
        ui = Dashboard("/tmp", demo)
        ui.pointer_supported = True
        ui.update()
        ui.switch("thread:demo-active")
        ui.buffer, ui.cursor = "keep my draft", 4
        before = (ui.selected, ui.focus, ui.buffer, ui.cursor, ui.scroll, ui.view)
        with patch("curses.curs_set"), patch("sys.stdout") as output, patch("tyrell.ui.copy_text") as copy:
            ui.render(Screen(38, 120))
            text_y, row = next((y, row) for y, row in ui.history_cells.items() if row["text"])
            points = [(4, ui.hit_rows[0][0], "pointer"),
                      (ui.hit_tabs[1][0], ui.tab_y, "pointer"),
                      (row["x"], text_y, "text"),
                      (ui.draft_left, ui.draft_top, "text"),
                      (ui.sidebar_width, 12, "default")]
            for x, y, shape in points:
                for char in "\x1b[<35;%d;%dM" % (x+1, y+1):
                    ui.input_key(char)
                self.assertEqual(ui.pointer_shape, shape)
                self.assertIsNone(ui.selection_anchor)
                self.assertEqual((ui.selected, ui.focus, ui.buffer, ui.cursor, ui.scroll, ui.view), before)
            copy.assert_not_called()
            count = output.write.call_count
            ui.mouse(35, ui.sidebar_width, 13)
            self.assertEqual(output.write.call_count, count)  # No repeated output for the same shape.
            ui.mouse(35, 4, ui.hit_rows[0][0])
            ui.panel = "Help"
            ui.render(Screen(38, 120))
            self.assertEqual(ui.pointer_shape, "default")
            ui.mouse_enabled = False
            ui.update_pointer()
            self.assertEqual(ui.pointer_shape, "text")

    def test_hover_reporting_is_only_enabled_for_supported_terminals(self):
        ui = Dashboard("/tmp", {"threads": {}})
        with patch("curses.mousemask"), patch("sys.stdout") as output:
            ui.pointer_supported = False
            ui.configure_mouse()
            written = "".join(call.args[0] for call in output.write.call_args_list)
            self.assertIn("\x1b[?1002h", written)
            self.assertNotIn("\x1b[?1003h", written)
            self.assertNotIn("\x1b]22;", written)
            output.reset_mock()
            ui.pointer_supported = True
            ui.configure_mouse()
            written = "".join(call.args[0] for call in output.write.call_args_list)
            self.assertIn("\x1b[?1003h", written)
            self.assertIn("\x1b]22;default\x1b\\", written)

    def test_agent_markdown_is_styled_and_copyable_without_markup(self):
        text = "Starta om vyn med **Ctrl+Q** och `tyrell`.\n\n```json\n{\"text\": \"**literal**\"}\n```"
        rows = timeline([{"type": "agentMessage", "text": text}], 90, "chat", wrap, crop)
        body = "\n".join(row["copy_text"] for row in rows if row.get("copy_text") is not None)
        self.assertIn("Starta om vyn med Ctrl+Q och tyrell.", body)
        self.assertNotIn("**Ctrl+Q**", body)
        self.assertNotIn("`codex", body)
        self.assertNotIn("```", body)
        self.assertIn('{"text": "**literal**"}', body)
        spans = [span for row in rows for span in row.get("spans", [])]
        self.assertIn(("Ctrl+Q", "strong"), spans)
        self.assertIn(("tyrell", "inlinecode"), spans)

    def test_json_tool_output_does_not_appear_after_returning_to_chat(self):
        data = {"connected": True, "threads": {"a": {"id": "a", "name": "Agent", "status": {"type": "idle"}, "items": [
            {"id": "tool", "type": "commandExecution", "text": '$ test\n{"text": "TOOL_JSON_MARKER"}'},
            {"id": "answer", "type": "agentMessage", "text": "Ordinary answer"}]}}}
        ui = Dashboard("/tmp", data)
        ui.update()
        screen = Screen(38, 120)
        screen.clearok = unittest.mock.Mock()
        with patch("curses.curs_set"):
            ui.view = "tools"
            ui.render(screen)
            self.assertIn("TOOL_JSON_MARKER", "\n".join(screen.writes))
            ui.return_to_chat()
            ui.render(screen)
            self.assertNotIn("TOOL_JSON_MARKER", "\n".join(screen.writes))
            self.assertIn("Ordinary answer", "\n".join(screen.writes))
        self.assertEqual(screen.clearok.call_count, 2)

    def test_tab_history_select_and_copy_without_changing_draft(self):
        items = [{"id": str(i), "type": "agentMessage", "text": "History line %d" % i} for i in range(20)]
        ui = Dashboard("/tmp", {"threads": {"a": {"id": "a", "name": "Agent", "items": items, "status": {"type": "idle"}}}})
        ui.update()
        ui.buffer, ui.cursor = "unsent draft", 4
        ui.key("\t")
        self.assertEqual(ui.focus, "history")
        ui.key(curses.KEY_UP)
        self.assertEqual(ui.scroll, 1)
        ui.key("x")
        with patch("curses.curs_set"), patch("tyrell.ui.copy_text") as copy:
            ui.render(Screen(38, 120))
            y, row = next((y, row) for y, row in ui.history_cells.items() if row["text"])
            ui.mouse(0, row["x"], y)
            ui.mouse(32, row["x"] + 7, y)
            for char in "\x1b[<0;%d;%dm" % (row["x"] + 8, y + 1):
                ui.input_key(char)
            copy.assert_not_called()
            self.assertFalse(ui.selection_dragging)
            self.assertIsNotNone(ui.selection_anchor)
            notice = ui.notice
            for char in "\x1b[99;9u":
                ui.input_key(char)
            copy.assert_called_once_with("History")
            # A release or repeat must not copy a second time.
            for char in "\x1b[99;9:3u\x1b[99;9:2u":
                ui.input_key(char)
            copy.assert_called_once_with("History")
            ui.render(Screen(38, 120))
            self.assertEqual(ui.notice, notice)
        self.assertEqual((ui.buffer, ui.cursor), ("unsent draft", 4))
        ui.key("\t")
        self.assertEqual(ui.focus, "chat")
        self.assertIsNone(ui.selection_anchor)
        ui.key("\t")
        self.assertEqual(ui.focus, "sidebar")

    def test_scrollbar_click_drag_and_release_browse_history_without_editing(self):
        items = [{"id": str(i), "type": "agentMessage", "text": "Message %d" % i} for i in range(40)]
        ui = Dashboard("/tmp", {"connected": True, "threads": {"a": {"id": "a", "name": "Agent", "items": items, "status": {"type": "idle"}}}})
        ui.update()
        ui.buffer, ui.cursor = "keep", 4
        with patch("curses.curs_set"):
            ui.render(Screen(38, 120))
            bar = ui.scrollbar
            self.assertGreater(bar["maximum"], 0)
            self.assertEqual(ui.scroll, 0)
            ui.mouse(0, bar["x"], bar["top"])
            ui.render(Screen(38, 120))
            self.assertEqual(ui.scroll, ui.scrollbar["maximum"])
            ui.mouse(32, bar["x"], bar["top"] + bar["height"] - 1)
            ui.render(Screen(38, 120))
            self.assertEqual(ui.scroll, 0)
            for char in "\x1b[<0;%d;%dm" % (bar["x"] + 1, bar["top"] + 1):
                ui.input_key(char)
            self.assertIsNone(ui.scrollbar_grab)
            self.assertEqual((ui.buffer, ui.cursor), ("keep", 4))

    def test_mouse_focuses_empty_sidebar_selects_agents_and_opens_each_tab(self):
        for height in (24, 38):
            with self.subTest(height=height), patch("curses.curs_set"):
                ui = Dashboard("/tmp", {"threads": {key: {"id": key, "name": key,
                    "status": {"type": "idle"}} for key in ("a", "b")}})
                ui.update()
                ui.buffer, ui.cursor, ui.focus = "draft a", 7, "chat"
                screen = Screen(height, 120)
                ui.render(screen)

                def click(x, y):
                    for char in "\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x+1, y+1, x+1, y+1):
                        ui.input_key(char)
                    ui.render(screen)

                click(2, 3)  # The sidebar heading also focuses the sidebar.
                self.assertEqual((ui.focus, ui.selected, ui.buffer), ("sidebar", "thread:a", "draft a"))
                click(4, ui.hit_rows[1][0])
                self.assertEqual((ui.focus, ui.selected, ui.buffer), ("sidebar", "thread:b", ""))
                click(5, ui.hit_rows[0][0])  # Status dot shares the one-line agent row.
                self.assertEqual((ui.selected, ui.buffer), ("thread:a", "draft a"))
                for view in ("plan", "tools", "chat"):
                    left, right, _ = next(hit for hit in ui.hit_tabs if hit[2] == view)
                    click((left + right) // 2, ui.tab_y)
                    self.assertEqual((ui.view, ui.focus, ui.buffer), (view, "tabs", "draft a"))

    def test_plan_messages_are_hidden_but_normal_replies_and_user_text_remain(self):
        plan = "Plan:\n- [>] Inspect the code\n- [ ] Run tests\nETA: 1-3 min"
        items = [{"type": "agentMessage", "text": plan},
                 {"type": "agentMessage", "text": plan + "\n\nThe fix is ready.\nYour settings are preserved."},
                 {"type": "userMessage", "text": "Plan:\n- [ ] My own checklist"},
                 {"type": "agentMessage", "text": "A normal reply with a checklist:\n- [x] Keep this text."}]
        chat = "\n".join(row["text"] for row in timeline(items, 90, "chat", wrap, crop))
        self.assertNotIn("Inspect the code", chat)
        self.assertNotIn("Run tests", chat)
        self.assertNotIn("ETA:", chat)
        self.assertIn("The fix is ready.", chat)
        self.assertIn("Your settings are preserved.", chat)
        self.assertIn("My own checklist", chat)
        self.assertIn("Keep this text", chat)
        for partial in ("Plan:", "Plan:\n- [", "Plan:\n- [>] Inspect\n- [", "Plan:\n- [x] Inspect\nETA: 1-"):
            self.assertFalse(timeline([{"type": "agentMessage", "text": partial}], 90, "chat", wrap, crop))

    def test_sidebar_names_are_yellow_underlined_with_dot_on_same_row(self):
        demo = json.loads((Path(__file__).resolve().parents[1] / "demo.json").read_text())
        ui = Dashboard("/tmp", demo)
        ui.update()
        ui.switch("thread:demo-active")
        ui.styles["agentname"], ui.styles["selected"] = 256, 512
        screen = Screen(38, 120)
        with patch("curses.curs_set"):
            ui.render(screen)
        top = next(top for top, _, key in ui.hit_rows if key == ui.selected)
        name = next(row for row in screen.positions if row[0] == top and row[1] == 7)
        self.assertEqual(name[3], 256 | curses.A_UNDERLINE)
        self.assertFalse(any(y == top and text == "▶" for y, x, text, style in screen.positions))
        status = next(row for row in screen.positions if row[0] == top and row[1] == 5 and row[2] == "●")
        self.assertEqual(status[1], name[1] - 2)
        for y, x, text, style in screen.positions:
            if x < ui.sidebar_width and 3 <= y < 20:
                self.assertNotEqual(style, ui.styles["selected"])
        self.assertNotIn("Inspect the current authentication flow", "\n".join(screen.writes))
        self.assertNotIn("Replace session middleware", "\n".join(screen.writes))
        ui.styles["accent"], ui.styles["muted"] = 1024, 2048
        ui.styles["historyfocus"] = 4096
        with patch("curses.curs_set"):
            for focus, color in (("sidebar", 1024), ("history", 2048), ("chat", 2048)):
                ui.focus = focus
                ui.render(screen)
                divider = [style for y, x, text, style in screen.positions if x == ui.sidebar_width and text == "│"]
                self.assertTrue(divider)
                self.assertEqual(set(divider), {color})
                history_divider = [style for y, x, text, style in screen.positions
                                   if y == ui.tab_y + 2 and x == ui.sidebar_width + 2 and len(text) > 20]
                self.assertEqual(history_divider, [(4096 if focus == "history" else 2048) | curses.A_UNDERLINE])
                self.assertTrue(any(y == 3 and text == "[Esc]" for y, x, text, style in screen.positions))

    def test_status_colors_and_labels_match_user_request(self):
        for key in ("working", "quiet (active)"):
            self.assertEqual((STATUS[key], status_tone(key)), ("Working", "working"))
        for key in ("idle", "saved"):
            self.assertEqual((STATUS[key], status_tone(key)), ("Ready", "success"))
        for key in ("approval", "question"):
            self.assertEqual((STATUS[key], status_tone(key)), ("Waiting", "warning"))
        self.assertEqual(PALETTE["working"][2], curses.COLOR_BLUE)
        self.assertNotEqual(PALETTE["user"], PALETTE["agent"])

    def test_message_stays_in_chat_plan_is_visible_and_sidebar_order_is_stable(self):
        demo = json.loads((Path(__file__).resolve().parents[1] / "demo.json").read_text())
        ui = Dashboard("/tmp", demo)
        ui.update()
        ui.switch("thread:demo-active")
        order = [key for key, _ in ui.rows]
        thread = ui.current()
        message = thread["items"][0]["text"]
        thread["activity"] = message
        with patch("curses.curs_set"):
            screen = Screen(38, 120)
            ui.render(screen)
        occurrences = [(y, x) for y, x, text, _ in screen.positions if message in text]
        self.assertEqual(len(occurrences), 1)
        self.assertGreater(occurrences[0][0], ui.tab_y + 2)
        self.assertGreater(occurrences[0][1], ui.sidebar_width)
        self.assertNotIn("YOU blue", "\n".join(screen.writes))
        ui.key(curses.KEY_F2)
        with patch("curses.curs_set"):
            ui.render(screen)
        for step in thread["plan"]:
            self.assertIn(step["step"], "\n".join(screen.writes))
        demo["threads"]["demo-idle"].update(status={"type": "active"}, updatedAt=9999999999)
        ui.update()
        self.assertEqual([key for key, _ in ui.rows], order)

    def test_raw_terminal_reports_never_become_draft_text(self):
        ui = Dashboard("/tmp", {"threads": {}, "tasks": []})
        ui.buffer, ui.cursor = "keep draft", 4
        reports = ["\x1b[<64;60;12M", "\x1b[<68;60;22M", "\x1b[M" + chr(96) + chr(92) + chr(44), "\x1b[96;60;12M"]
        for report in reports:
            for char in report:
                ui.input_key(char)
        self.assertEqual(ui.scroll, 12)
        for report in ("\x1b[<64;60;12m", "\x1b[<35;60;12M", "\x1b[?999;12c"):
            for char in report:
                ui.input_key(char)
        self.assertEqual(ui.scroll, 12)
        self.assertEqual((ui.buffer, ui.cursor), ("keep draft", 4))
        for char in "\x1b[6~":
            ui.input_key(char)
        self.assertEqual(ui.scroll, 2)
        ui.focus = "chat"
        for char in "\x1b[D":
            ui.input_key(char)
        self.assertEqual(ui.cursor, 3)

    def test_rename_command_and_form_target_the_selected_agent(self):
        ui = Dashboard("/tmp", {"threads": {"a": {"id": "a", "name": "Old", "status": {"type": "idle"}}}})
        ui.update()
        with patch.object(ui, "submit") as submit:
            ui.buffer = "/rename Nytt namn"
            ui.entered()
            submit.assert_called_with("rename", threadId="a", name="Nytt namn")
            ui.buffer = "/rename"
            ui.entered()
            self.assertEqual(ui.wizard["kind"], "rename")
            ui.buffer = "My agent's name"
            ui.entered()
            submit.assert_called_with("rename", threadId="a", name="My agent's name")
            self.assertIsNone(ui.wizard)

    def test_f5_renames_without_sending_or_losing_the_chat_draft(self):
        ui = Dashboard("/tmp", {"threads": {"a": {"id": "a", "name": "Original", "status": {"type": "idle"}}}})
        ui.update()
        ui.buffer, ui.cursor = "unsent chat draft", 17
        with patch.object(ui, "submit") as submit:
            for key in "\x1b[15~":
                ui.input_key(key)
            self.assertEqual(ui.wizard["kind"], "rename")
            self.assertEqual(ui.buffer, "")
            for key in "Nytt namn\r":
                ui.input_key(key)
            submit.assert_called_once_with("rename", threadId="a", name="Nytt namn")
            self.assertEqual(ui.buffer, "unsent chat draft")
            ui.key(curses.KEY_F5)
            ui.insert_text("cancelled name")
            ui.key("\x1b")
            self.assertEqual(ui.buffer, "unsent chat draft")
            self.assertIsNone(ui.wizard)

    def test_plan_checklist_updates_live_scrolls_and_keeps_draft(self):
        steps = [{"step": "Task %d" % i, "status": "pending"} for i in range(30)]
        data = {"connected": True, "threads": {"a": {"id": "a", "name": "Agent", "plan": steps, "status": {"type": "active"}}}}
        ui = Dashboard("/tmp", data)
        ui.update()
        ui.buffer = "my draft"
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "plan")
        screen = Screen(38, 120)
        with patch("curses.curs_set"):
            ui.render(screen)
            self.assertIn("[ ] 1. Task 0", "\n".join(screen.writes))
            steps[0]["status"], steps[1]["status"] = "completed", "inProgress"
            ui.render(screen)
            self.assertIn("[✓] 1. Task 0 · Done", "\n".join(screen.writes))
            self.assertIn("[▶] 2. Task 1 · In progress", "\n".join(screen.writes))
            ui.mouse(65, 90, 15)
            ui.render(screen)
            self.assertEqual(ui.plan_scroll, 3)
        self.assertEqual(ui.buffer, "my draft")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "tools")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "files")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "processes")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "setup")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "chat")
        ui.key("\x0f")
        self.assertEqual(ui.view, "tools")
        ui.key("\x1b")
        self.assertEqual((ui.view, ui.focus, ui.buffer), ("chat", "chat", "my draft"))

    def test_conversation_and_output_never_mix(self):
        items = [{"type": kind, "text": text} for kind, text in [
            ("userMessage", "my request"), ("agentMessage", "agent reply"),
            ("commandExecution", "$ test\noutput"), ("reasoning", "working on it")]]
        chat = timeline(items, 60, "chat", wrap, crop)
        logs = timeline(items, 60, "tools", wrap, crop)
        self.assertEqual({r["tone"] for r in chat}, {"user", "agent", "base"})
        self.assertNotIn("output", "\n".join(r["text"] for r in chat))
        self.assertNotIn("agent reply", "\n".join(r["text"] for r in logs))
        self.assertIn("$ test", "\n".join(r["text"] for r in logs))

    def test_long_message_scrolls_without_a_sticky_header_or_hidden_last_row(self):
        rows = timeline([{"type": "userMessage", "text": "\n".join("Line " + str(i) for i in range(30))}], 60, "chat", wrap, crop)
        visible, _ = viewport(rows, 8, 10)
        self.assertFalse(visible[0]["header"])
        self.assertEqual([r["text"] for r in visible], [r["text"] for r in rows[-18:-10]])
        following, _ = viewport(rows, 8, 9)
        self.assertEqual(visible[1:], following[:-1])

    def test_waiting_is_static_while_working_animates(self):
        self.assertNotEqual(activity_indicator("working", 1), activity_indicator("working", 2))
        for status in ("approval", "question", "offline", "idle", "quiet (active)"):
            self.assertEqual(activity_indicator(status, 1), activity_indicator(status, 2))
        self.assertIn("thinking", activity_indicator("working", 1, thinking=True))

    def test_mouse_wheel_scrolls_without_changing_or_sending_the_draft(self):
        ui = Dashboard("/tmp", {"threads": {}, "tasks": []})
        ui.buffer = "unsent text"
        for char in "\x1b[<64;60;12M":
            ui.input_key(char)
        self.assertEqual(ui.scroll, 3)
        for char in "\x1b[<65;60;12M":
            ui.input_key(char)
        self.assertEqual(ui.scroll, 0)
        self.assertEqual(ui.buffer, "unsent text")
        self.assertTrue(ui.commands.empty())

    def test_syntax_colors_preserve_original_output(self):
        text = 'const count = 42; print("hello") # note'
        spans = syntax_spans(text)
        self.assertEqual("".join(part for part, _ in spans), text)
        self.assertTrue({"keyword", "number", "string", "comment"}.issubset({tone for _, tone in spans}))
        self.assertEqual(syntax_spans("ERROR: test failed")[0][1], "logerror")

    def test_sidebar_enter_never_sends_a_draft_and_view_switch_preserves_it(self):
        ui = Dashboard("/tmp", {"threads": {}, "tasks": []})
        ui.buffer, ui.cursor = "unfinished draft", 16
        with patch.object(ui, "submit") as submit:
            ui.key("\n")
            submit.assert_not_called()
        self.assertEqual(ui.focus, "chat")
        ui.key(curses.KEY_F2)
        self.assertEqual(ui.view, "plan")
        self.assertEqual(ui.buffer, "unfinished draft")

    def test_archive_browser_routes_reads_and_restore_to_selected_agent(self):
        ui = Dashboard("/tmp", {"threads": {}, "tasks": [], "archived": {"a": {"id": "a", "name": "Saved agent", "status": {"type": "idle"}}}})
        with patch.object(ui, "submit") as submit:
            ui.browser("archive")
            ui.update()
            self.assertEqual(ui.selected, "archive:a")
            submit.assert_any_call("read_archive", threadId="a")
            ui.buffer = "/restore"
            ui.entered()
            submit.assert_called_with("restore", threadId="a")
            ui.buffer = "do not start this"
            submit.reset_mock()
            ui.entered()
            submit.assert_not_called()

    def test_render_at_small_and_large_sizes_and_on_a_planned_task(self):
        demo = json.loads((Path(__file__).resolve().parents[1] / "demo.json").read_text())
        ui = Dashboard("/tmp", demo)
        ui.update()
        with patch("curses.curs_set"):
            for size in ((18, 70), (24, 80), (38, 120)):
                for view in ("chat", "tools"):
                    ui.view = view
                    screen = Screen(*size)
                    ui.render(screen)
                    self.assertIn("CHAT", " ".join(screen.writes).upper())
            ui.switch("task:task-next")
            ui.render(Screen(24, 80))
