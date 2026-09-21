"""Isolated demo used by verify_native_terminal.py; never connects to an agent."""
import curses
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tyrell.ui import Dashboard
from tyrell.clipboard import selection_text

out = Path(sys.argv[1])
thread = {"id": "clipboard-fixture", "name": "Clipboard verification", "status": {"type": "idle"},
          "items": [{"id": "reply", "type": "agentMessage", "text": "Alpha åäö\n\nBeta 猫"}]}


class Fixture(Dashboard):
    def key(self, key):
        if key == "\x14":  # Inject a simulated streamed reply through the test fixture only.
            thread["items"].append({"id": "new-reply", "type": "agentMessage", "text": "NEW STREAMED REPLY"})
            return
        super().key(key)

    def render(self, screen):
        super().render(screen)
        points = {}
        for y, row in self.history_cells.items():
            for word in ("Alpha", "Beta", "NEW STREAMED REPLY"):
                if row["text"].startswith(word):
                    points[word] = [row["x"], y]
        highlighted = False
        if self.selection_anchor is not None and self.selection_end is not None:
            first, last = sorted((self.selection_anchor, self.selection_end))
            for y, row in self.history_cells.items():
                if row["index"] == first[0] and first != last:
                    from tyrell.ui import cells
                    x = row["x"] + cells(row["text"][:first[1]])
                    highlighted = (screen.inch(y, x) & curses.A_ATTRIBUTES) == (self.styles["selected"] & curses.A_ATTRIBUTES)
        state = {"highlighted": highlighted, "pid": os.getpid(), "terminal": os.environ.get("TERM_PROGRAM"), "points": points,
                 "copy": self.hit_copy, "textView": self.hit_text_view,
                 "selected": selection_text(self.history_rows, self.selection_anchor, self.selection_end),
                 "dragging": self.selection_dragging, "draft": self.buffer, "notice": self.notice,
                 "nativeMode": self.native_selection_mode()}
        temporary = out.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        temporary.replace(out)


ui = Fixture(out.parent, {"connected": True, "threads": {thread["id"]: thread}, "tasks": [], "requests": []})
ui.selected = "thread:" + thread["id"]
ui.rows = [(ui.selected, thread)]
ui.focus = "history"
curses.wrapper(ui.run)
out.with_suffix('.exited').write_text('clean exit')
