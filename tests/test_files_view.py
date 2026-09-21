import curses
import tempfile
import unittest
from unittest.mock import patch

from tyrell.files_view import record_changes, status
from tyrell.state import State
from tyrell.ui import Dashboard
from test_presentation import Screen


def change(path="src/app.py", kind=None, state="completed", iid="patch", diff="@@ -1 +1 @@\n-old\n+new"):
    return {"id": iid, "type": "fileChange", "status": state,
            "changes": [{"path": path, "kind": kind or {"type": "update"}, "diff": diff}]}


class FileStateTests(unittest.TestCase):
    def test_native_add_and_delete_contents_become_colored_patch_lines(self):
        thread = {"cwd": "/repo"}
        for kind, prefix in (("add", "+"), ("delete", "-")):
            record_changes(thread, change(kind={"type": kind}, diff="first line\nsecond line\n"))
            record = thread["changedFiles"]["/repo/src/app.py"]
            self.assertEqual(record["diff"], prefix + "first line\n" + prefix + "second line")
            self.assertEqual(record["added"] if kind == "add" else record["removed"], 2)

    def test_history_live_completion_retention_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(directory)
            thread = state.merge_thread({"id": "a", "cwd": "/repo", "turns": [
                {"id": "old", "status": "completed", "items": [change()]},
                {"id": "new", "status": "inProgress", "items": [change("infra/main.tf", state="inProgress", iid="next")]}]}, history=True)
            state.event("item/completed", {"threadId": "a", "item": change("infra/main.tf", iid="next")})
            self.assertEqual(len(thread["changedFiles"]), 2)
            self.assertEqual(thread["changedFiles"]["/repo/infra/main.tf"]["status"], "completed")
            for i in range(260):
                state.put_item(thread, {"id": str(i), "type": "agentMessage", "text": "done"})
            self.assertEqual(len(thread["items"]), 250)
            state.save()
            self.assertEqual(State(directory).thread("a")["changedFiles"], thread["changedFiles"])
            state.merge_thread({"id": "a", "turns": [{"id": "t", "status": "completed", "items": [change()]}]}, history=True)
            self.assertEqual(list(thread["changedFiles"]), ["/repo/src/app.py"])

    def test_rename_normalization_failed_changes_and_preview_limit(self):
        thread = {"cwd": "/repo"}
        record_changes(thread, change())
        record_changes(thread, change("./src/app.py", {"type": "update", "move_path": "lib/app.py"}))
        self.assertNotIn("/repo/src/app.py", thread["changedFiles"])
        renamed = thread["changedFiles"]["/repo/lib/app.py"]
        self.assertEqual((renamed["from"], status(renamed)[0]), ("/repo/src/app.py", "Renamed"))
        for outcome, label in (("failed", "Failed"), ("declined", "Declined"), ("inProgress", "Pending")):
            record_changes(thread, change("../outside.txt", state=outcome, diff="+a\n" * 30000))
            record = thread["changedFiles"]["/outside.txt"]
            self.assertEqual(status(record)[0], label)
            self.assertTrue(record["truncated"])
            self.assertEqual((len(record["diff"]), record["added"]), (60000, 30000))


class FilesUITests(unittest.TestCase):
    def make_ui(self):
        thread = {"id": "a", "name": "Agent", "cwd": "/repo", "status": {"type": "idle"}}
        for event in (change(), change("infra/main.tf", {"type": "add"}), change("/outside/readme.txt", {"type": "delete"})):
            record_changes(thread, event)
        ui = Dashboard("/tmp", {"connected": True, "threads": {"a": thread}})
        ui.update()
        ui.view, ui.focus = "files", "history"
        ui.buffer, ui.cursor = "unsent draft", 4
        return ui

    def render(self, ui, height=38, width=120):
        with patch("curses.curs_set"):
            screen = Screen(height, width)
            ui.render(screen)
        return screen

    def test_tree_mouse_collapse_patch_colors_and_draft(self):
        ui = self.make_ui()
        self.render(ui)
        texts = [r["text"] for r in ui.files.rows]
        self.assertTrue(any("Outside project/" in r for r in texts))
        self.assertTrue(any("Added  main.tf" in r for r in texts))
        folder = next(i for i, r in enumerate(ui.files.rows) if r["action"] == ("folder", ("src",)))
        y = next(y for y, i in ui.files.hits.items() if i == folder)
        ui.mouse(0, ui.history_bounds[0] + 2, y)
        self.render(ui)
        self.assertFalse(any("app.py" in r["text"] for r in ui.files.rows))
        ui.key(curses.KEY_RIGHT)
        self.render(ui)
        ui.key(curses.KEY_DOWN)
        ui.key("\r")
        self.render(ui)
        self.assertEqual(ui.files.detail, "/repo/src/app.py")
        self.assertIn(("+new", "success"), [(r["text"], r["tone"]) for r in ui.files.rows])
        self.assertIn(("-old", "error"), [(r["text"], r["tone"]) for r in ui.files.rows])
        self.assertTrue(ui.history_cells)
        ui.key(curses.KEY_LEFT)
        self.render(ui)
        self.assertIsNone(ui.files.detail)
        self.assertEqual((ui.buffer, ui.cursor), ("unsent draft", 4))

    def test_tab_arrows_focus_wrap_escape_and_narrow_header(self):
        ui = self.make_ui()
        ui.view, ui.focus = "chat", "chat"
        ui.key(curses.KEY_F2)
        self.assertEqual((ui.view, ui.focus), ("plan", "tabs"))
        for view in ("tools", "files", "processes", "setup", "chat"):
            ui.key(curses.KEY_RIGHT)
            self.assertEqual((ui.view, ui.focus), (view, "tabs"))
        ui.key(curses.KEY_LEFT)
        self.assertEqual(ui.view, "setup")
        ui.key("x")
        ui.key(curses.KEY_BACKSPACE)
        self.assertEqual((ui.buffer, ui.cursor), ("unsent draft", 4))
        ui.key(curses.KEY_DOWN)
        self.assertEqual(ui.focus, "history")
        ui.key(curses.KEY_F2)
        ui.key("\t")
        self.assertEqual(ui.focus, "history")
        ui.key("\x1b")
        self.assertEqual((ui.view, ui.focus), ("chat", "chat"))
        self.render(ui, 24, 70)
        self.assertLessEqual(ui.hit_tabs[-1][1], 68)
        ui.mouse(0, ui.hit_tabs[3][0], ui.tab_y)
        self.assertEqual((ui.view, ui.focus), ("files", "tabs"))
        ui.key("\r")
        self.assertEqual(ui.focus, "history")

    def test_scrollbar_and_switching_agents_reset_file_details(self):
        ui = self.make_ui()
        record_changes(ui.current(), change(diff="+line\n" * 100))
        self.render(ui)
        index = next(i for i, r in enumerate(ui.files.rows) if r["action"] == ("file", "/repo/src/app.py"))
        ui.files.activate(index)
        self.render(ui)
        ui.mouse(65, ui.history_bounds[0] + 2, ui.history_bounds[1] + 4)
        self.render(ui)
        self.assertGreater(ui.files.scroll, 0)
        ui.seek_scrollbar(ui.scrollbar["top"] + ui.scrollbar["height"] - 1)
        self.render(ui)
        self.assertEqual(ui.files.scroll, ui.scrollbar["maximum"])
        ui.data["threads"]["b"] = {"id": "b", "status": {"type": "idle"}, "items": []}
        ui.update()
        ui.switch("thread:b")
        self.render(ui)
        self.assertIsNone(ui.files.detail)
        self.assertTrue(any("No file changes" in r["text"] for r in ui.files.rows))
