"""Regression coverage for Apple Terminal mouse selection through the input decoder."""
import curses
import unittest
from unittest.mock import patch

from codex_dashboard.clipboard import selection_text
from codex_dashboard.ui import Dashboard


class Screen:
    def __init__(self, width=120, height=40):
        self.width, self.height, self.draws = width, height, []

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text, style=0):
        self.draws.append((y, x, text, style))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def fixture():
    thread = {"id": "fixture", "name": "Clipboard fixture", "status": {"type": "idle"},
              "items": [{"id": "reply", "type": "agentMessage", "text": "Alpha åäö\n\nBeta 猫"}]}
    ui = Dashboard("/tmp/hub-clipboard-fixture", {"connected": True, "threads": {"fixture": thread}, "tasks": [], "requests": []})
    ui.native_terminal, ui.pointer_supported = True, False
    ui.selected, ui.rows = "thread:fixture", [("thread:fixture", thread)]
    ui.focus, ui.view = "history", "chat"
    ui.styles["selected"] = 1234
    return ui, thread


def report(ui, button, x, y, protocol="sgr", release=False):
    if protocol == "sgr":
        sequence = "\x1b[<%d;%d;%d%s" % (button, x + 1, y + 1, "m" if release else "M")
    elif protocol == "x10":
        sequence = "\x1b[M" + chr(32 + (3 if release else button)) + chr(x + 33) + chr(y + 33)
    else:
        sequence = "\x1b[%d;%d;%dM" % (32 + (3 if release else button), x + 1, y + 1)
    for char in sequence:
        ui.input_key(char)


def point(ui, prefix, column=0):
    y, row = next((y, row) for y, row in ui.history_cells.items() if row["text"].startswith(prefix))
    return row["x"] + column, y


class NativeClipboardTests(unittest.TestCase):
    def test_drag_highlights_and_copy_copies_only_selected_text_for_mouse_protocols(self):
        for protocol in ("sgr", "x10", "rxvt"):
            with self.subTest(protocol=protocol), patch("codex_dashboard.ui.copy_text") as copy:
                ui, _ = fixture()
                screen = Screen()
                ui.render(screen)
                a, b = point(ui, "Alpha", 6), point(ui, "Beta", 7)
                report(ui, 0, *a, protocol=protocol)
                report(ui, 32, *b, protocol=protocol)
                report(ui, 0, *b, protocol=protocol, release=True)
                self.assertFalse(ui.selection_dragging)
                self.assertEqual(selection_text(ui.history_rows, ui.selection_anchor, ui.selection_end), "åäö\n\nBeta 猫")
                copy.assert_not_called()  # Selecting never copies automatically.
                ui.render(screen)
                self.assertTrue(any(style == 1234 and text for _, _, text, style in screen.draws))
                left, _, y = ui.hit_copy
                report(ui, 0, left, y, protocol=protocol)
                copy.assert_called_once_with("åäö\n\nBeta 猫")
                self.assertIsNone(ui.history_snapshot)
                self.assertFalse(ui.native_copy_mode)

    def test_reverse_selection_and_single_click(self):
        ui, _ = fixture()
        ui.render(Screen())
        a, b = point(ui, "Alpha"), point(ui, "Alpha", 5)
        report(ui, 0, *a)
        report(ui, 0, *a, release=True)
        self.assertIsNone(ui.history_snapshot)
        report(ui, 0, *b)
        report(ui, 32, *a)
        report(ui, 0, *a, release=True)
        self.assertEqual(selection_text(ui.history_rows, ui.selection_anchor, ui.selection_end), "Alpha")

    def test_reply_arriving_after_drag_appears_before_copy_without_changing_selection(self):
        ui, thread = fixture()
        ui.render(Screen())
        a, b = point(ui, "Alpha"), point(ui, "Alpha", 5)
        report(ui, 0, *a)
        report(ui, 32, *b)
        report(ui, 0, *b, release=True)
        thread["items"].append({"id": "new", "type": "agentMessage", "text": "NEW STREAMED REPLY"})
        screen = Screen()
        ui.render(screen)
        self.assertTrue(any("NEW STREAMED REPLY" in text for _, _, text, _ in screen.draws))
        with patch("codex_dashboard.ui.copy_text") as copy:
            left, _, y = ui.hit_copy
            report(ui, 0, left, y)
            copy.assert_called_once_with("Alpha")
        screen = Screen()
        ui.render(screen)
        self.assertTrue(any("NEW STREAMED REPLY" in text for _, _, text, _ in screen.draws))

    def test_trimmed_history_clears_stale_selection_and_shows_new_reply(self):
        ui, thread = fixture()
        ui.render(Screen())
        a, b = point(ui, "Alpha"), point(ui, "Alpha", 5)
        report(ui, 0, *a)
        report(ui, 32, *b)
        report(ui, 0, *b, release=True)
        thread["items"] = [{"id": "new", "type": "agentMessage", "text": "NEW STREAMED REPLY"}]
        screen = Screen()
        ui.render(screen)
        self.assertIsNone(ui.selection_anchor)
        self.assertTrue(any("NEW STREAMED REPLY" in text for _, _, text, _ in screen.draws))
        with patch("codex_dashboard.ui.copy_text") as copy:
            ui.copy_selection()
            copy.assert_not_called()

    def test_copy_without_selection_does_nothing_and_text_view_is_separate(self):
        ui, _ = fixture()
        ui.render(Screen())
        with patch("codex_dashboard.ui.copy_text") as copy:
            left, _, y = ui.hit_copy
            report(ui, 0, left, y)
            copy.assert_not_called()
            self.assertFalse(ui.native_selection_mode())
        left, _, y = ui.hit_text_view
        report(ui, 0, left, y)
        self.assertTrue(ui.native_selection_mode())
        screen = Screen()
        ui.render(screen)
        self.assertFalse(any("│" in text or "╭" in text for _, _, text, _ in screen.draws))
        ui.key("\t")
        self.assertFalse(ui.native_selection_mode())

    def test_paste_remains_in_prompt_and_reply_stays_in_history(self):
        ui, thread = fixture()
        for char in "\x1b[200~my draft åäö\x1b[201~":
            ui.input_key(char)
        self.assertEqual(ui.buffer, "my draft åäö")
        screen = Screen()
        ui.render(screen)
        prompt = [text for y, _, text, _ in screen.draws if ui.draft_top <= y < ui.draft_top + ui.draft_height]
        self.assertTrue(any("my draft åäö" in text for text in prompt))
        self.assertFalse(any("Alpha" in text or "Beta" in text for text in prompt))

    def test_resize_clears_selection_instead_of_copying_wrong_rows(self):
        ui, _ = fixture()
        ui.render(Screen())
        a, b = point(ui, "Alpha"), point(ui, "Alpha", 5)
        report(ui, 0, *a)
        report(ui, 32, *b)
        report(ui, 0, *b, release=True)
        ui.render(Screen(width=80))
        # Resizing intentionally clears selection rather than copying different rows.
        self.assertIsNone(ui.selection_anchor)


if __name__ == "__main__":
    unittest.main()
