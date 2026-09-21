"""Exercise the real curses UI in a PTY using demo data; never connects to Codex."""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE = '''
import curses,json
from pathlib import Path
from tyrell.ui import Dashboard
import tyrell.ui as ui_module
copied = []
ui_module.copy_text = copied.append
data=json.loads(Path("demo.json").read_text())
t=data["threads"]["demo-active"]
t["items"] = [{"id":str(i),"type":"userMessage" if i%2==0 else "agentMessage","text":"Conversation line " + str(i)} for i in range(30)] + t["items"]
t["changedFiles"] = {"/demo/src/app.py": {"path": "/demo/src/app.py", "kind": "update", "status": "completed", "diff": "-old value\\n+new value", "added": 1, "removed": 1}}
t["cwd"] = "/demo"
ui=Dashboard("/tmp",data)
curses.wrapper(ui.run)
print("UI_CHECK_DRAFT=" + json.dumps(ui.buffer))
print("UI_CHECK_COPIED=" + json.dumps(copied))
'''


def main():
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 120, 0, 0))
    env = dict(os.environ, TERM=os.environ.get("UI_CHECK_TERM", "xterm-256color"))
    process = subprocess.Popen([sys.executable, "-B", "-c", CODE], stdin=slave, stdout=slave, stderr=slave, cwd=ROOT, env=env)
    os.close(slave)

    def read_for(seconds):
        end, output = time.monotonic() + seconds, bytearray()
        while time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], min(0.05, max(0, end - time.monotonic())))
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                output.extend(data)
        return output.decode(errors="replace")

    try:
        initial = read_for(0.8)
        assert "Tyrell Agent Management" in initial, "Application title is missing"
        assert "Codex Demo" in initial and "Copilot Demo" in initial, "Provider badges are missing"
        assert "YOU" in initial and "AGENT" in initial, "Missing speaker labels"
        assert "$ npm test" not in initial, "Tool output leaked into conversation"
        assert "PLAN" in initial, "Task list is missing"
        assert "█" in initial, "History scrollbar is missing"
        if env.get("TERM_PROGRAM", "").lower() == "ghostty" or env["TERM"] == "xterm-ghostty":
            assert "\x1b[?1003h" in initial, "Hover reporting is not enabled"
            os.write(master, b"\x1b[<35;5;6M")
            assert "\x1b]22;pointer\x1b\\" in read_for(0.2), "Agent hover did not show a hand"
            os.write(master, b"\x1b[<35;40;27M")
            assert "\x1b]22;text\x1b\\" in read_for(0.2), "History hover did not show a text cursor"
        os.write(master, b"\t")
        focused = read_for(0.3)
        assert "History" in focused, "Tab did not focus history"
        # A color escape alone does not repaint existing terminal cells. Require
        # the underlined spaces themselves: long Unicode runs vanished on macOS.
        import re
        focus_color = re.search(r"\x1b\[(?:0;)?4m\x1b\[38;5;153m(?:\x1b\[[0-9;]*m)*( +)(?:\x1b\[(\d+)b)?", focused)
        assert focus_color and len(focus_color[1]) + int(focus_color[2] or 0) >= 20, "History focus color was set without repainting its divider"
        os.write(master, b"\x1b[A")
        read_for(0.3)
        os.write(master, b"\x1b[B")
        read_for(0.3)
        # Select visible message text; system clipboard is replaced by a test spy.
        os.write(master, b"\x1b[<0;35;27M\x1b[<32;45;27M\x1b[<0;45;27m")
        selected = read_for(0.4)
        assert "Selection copied" not in selected, "Unexpected copying notice"
        os.write(master, b"\x1b[99;9u")
        assert "Selection copied" not in read_for(0.3), "Copying should be silent"
        os.write(master, b"\t")
        read_for(0.2)
        os.write(master, b"\x1bOQ")
        plan = read_for(0.4)
        assert "Done" in plan and "In progress" in plan and "Pending" in plan, "F2 did not open the live checklist"
        os.write(master, b"\x1bOQ")
        tools = read_for(0.4)
        assert "npm" in tools, "Second F2 did not open Tools"
        os.write(master, b"\x1b[D")
        assert "In progress" in read_for(0.3), "Left arrow did not return to Plan"
        os.write(master, b"\x1b[C")
        assert "npm" in read_for(0.3), "Right arrow did not return to Tools"
        os.write(master, b"\x1bOQ")
        files = read_for(0.4)
        assert "src/" in files and "app.py" in files, "Third F2 did not open the file tree"
        os.write(master, b"\x1b[B\x1b[B\r")
        diff = read_for(0.4)
        assert "Latest recorded patch" in diff and "new value" in diff, "File did not open its optional patch"
        os.write(master, b"\x1bOQ")
        setup = read_for(0.4)
        assert "Model & speed" in setup and "Terraform" in setup, "Fourth F2 did not open Setup"
        os.write(master, b"\x1b[<0;35;23M\x1b[<0;35;23m")
        expanded = read_for(0.3)
        assert "Terraform directory" in expanded, "Clicking the Terraform group did not expand its settings"
        os.write(master, b"\x1b[<0;35;25M\x1b[<0;35;25m")
        assert "Enter saves" in read_for(0.3), "Clicking a setup text field did not open the editor"
        os.write(master, b"infra")
        read_for(0.2)
        os.write(master, b"\x1bOQ")
        chat = read_for(0.4)
        assert "Delivered" in chat, "Fifth F2 did not return to Chat"
        os.write(master, b"\x1bOQ\x1b")
        chat = read_for(0.5)
        assert "Delivered" in chat, "Esc did not return from Plan to Chat"
        os.write(master, b"\x1b[15~")
        rename = read_for(0.4)
        assert "Rename agent" in rename and "Enter saves" in rename, "F5 did not open rename"
        os.write(master, b"New name\x1b")
        read_for(0.5)
        os.write(master, b"\x1b[<64;90;18M")
        scrolled = read_for(0.4)
        assert "History" in scrolled, "Mouse wheel did not scroll the chat"
        os.write(master, b"\x1b[<65;90;18M")
        returned = read_for(0.4)
        assert "Rename cancelled" in returned, "Mouse wheel down did not return to latest"
        # A slowly delivered report previously expired halfway through and leaked
        # its remaining coordinates into the input. The pointer is over the composer.
        for byte in b"\x1b[<64;90;33M":
            os.write(master, bytes([byte]))
            read_for(0.04)
        os.write(master, b"\x1b[<64;90;33M" * 20)
        read_for(0.3)
        os.write(master, b"\x1b[<65;90;33M" * 50)
        read_for(0.3)
        os.write(master, b"\x1b[5~")
        assert "History" in read_for(0.3), "Raw PageUp did not scroll history"
        os.write(master, b"\x1b[6~")
        read_for(0.3)
        os.write(master, b"/log\r")
        logs = read_for(0.4)
        assert "npm" in logs, "Tool view did not show the command"
        assert "38;5;117m" in logs, "Command syntax highlight was not applied"
        os.write(master, b"\x1b[<0;37;7M")
        chat = read_for(0.4)
        assert "Delivered" in chat, "Clicking the chat tab did not switch views"
        os.write(master, b"\x1b")
        read_for(0.4)
        os.write(master, b"first line\n\x1b\rsecond line\x1b[13;2uthird line")
        read_for(0.4)
        os.write(master, b"\x1b[113;5u")  # Ctrl+Q with enhanced keyboard reporting.
        closing = read_for(0.5)
        process.wait(timeout=3)
        assert process.returncode == 0, read_for(0.1)
        import json
        marker = closing.split("UI_CHECK_DRAFT=", 1)[1].splitlines()[0]
        assert json.loads(marker) == "first line\nsecond line\nthird line", "Multiline typing was altered or sent: " + marker
        copied_marker = closing.split("UI_CHECK_COPIED=", 1)[1].splitlines()[0]
        assert len(json.loads(copied_marker)) == 1, "Expected exactly one explicit copy"
        assert "\x1b[<u" in closing, "Keyboard protocol was not restored on exit"
        assert "\x1b[?1003l" in closing, "Hover reports were not disabled on exit"
        print("PTY passed: Tab to history, manual Cmd+C copy, scrollbar, F2 tabs, F5 rename, mouse reports, multiline draft, clean detach")
    finally:
        if process.poll() is None:
            process.terminate()
            read_for(0.2)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        os.close(master)


if __name__ == "__main__":
    main()
