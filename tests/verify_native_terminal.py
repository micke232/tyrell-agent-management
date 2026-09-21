"""Opt-in macOS integration check in a dedicated Apple Terminal window.

Uses Terminal's AppleScript input to exercise the real curses loop and pbcopy.
This verifies terminal input delivery, NOT physical mouse gestures or Cmd+C.
Run with macos_clipboard_guard.swift to preserve every original pasteboard type.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def applescript(source, *args):
    return subprocess.check_output(['/usr/bin/osascript', '-e', source, *args], text=True, timeout=15).strip()


def wait_for(predicate, description):
    until = time.monotonic() + 15
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('Timed out: ' + description)


def mouse(button, point, release=False):
    x, y = point
    return '\x1b[<%d;%d;%d%s' % (button, x + 1, y + 1, 'm' if release else 'M')


with tempfile.TemporaryDirectory(prefix='hub-native-clipboard-', dir='/tmp') as directory:
    state_path = Path(directory) / 'state.json'
    command = 'exec ' + ' '.join(shlex.quote(p) for p in (sys.executable, '-B', str(ROOT/'tests/native_terminal_fixture.py'), str(state_path)))
    window = applescript('''on run argv
        tell application "Terminal"
            set fixtureTab to do script (item 1 of argv)
            set custom title of fixtureTab to "Tyrell Agent Management clipboard verification"
            return id of front window
        end tell
    end run''', command)

    def send(text):
        applescript('''on run argv
            tell application "Terminal"
                do script (item 2 of argv) in selected tab of window id (item 1 of argv as integer)
            end tell
        end run''', window, text)

    def state():
        return json.loads(state_path.read_text()) if state_path.exists() else {}

    try:
        wait_for(lambda: 'Alpha' in state().get('points', {}), 'initial curses frame')
        assert state()['terminal'] == 'Apple_Terminal', state()['terminal']
        for reverse in (False, True):
            current = state()
            a = current['points']['Alpha']; a[0] += 6
            b = current['points']['Beta']; b[0] += 7
            start, end = (b, a) if reverse else (a, b)
            send(mouse(0, start) + mouse(32, end) + mouse(0, end, True))
            expected = 'åäö\n\nBeta 猫'
            wait_for(lambda: state().get('selected') == expected, 'multi-line selection')
            assert not state()['dragging']
            assert state()['highlighted'], 'Selected text is not visibly highlighted in the curses screen'
            if not reverse:
                send('\x14')
                wait_for(lambda: 'NEW STREAMED REPLY' in state().get('points', {}), 'reply visible with existing selection')
                assert state()['selected'] == expected
                assert state()['highlighted']
                print('PASS new reply visible before copying, with selection preserved')
            current = state(); left, _, y = current['copy']
            send(mouse(0, (left, y)) + mouse(0, (left, y), True))
            wait_for(lambda: not state().get('selected'), 'selection released after Copy')
            actual = subprocess.check_output(['/usr/bin/pbpaste']).decode('utf-8')
            assert actual == expected, 'System clipboard differs from selected fixture text'
            assert not state()['nativeMode'], 'Copy must not enter another view'
            print('PASS Apple Terminal → mouse reports → selection → Copy → real pbcopy/pbpaste (' + ('reverse' if reverse else 'forward') + ')')
        send('\x14')
        wait_for(lambda: 'NEW STREAMED REPLY' in state().get('points', {}), 'new reply displayed without restart')
        print('PASS new reply visible after copying without restarting')
        send('\x1b[200~Prompt åäö\x1b[201~')
        # Terminal appends Enter to do-script, so demo dispatch clears a submitted draft.
        wait_for(lambda: state().get('notice') == 'Demo · no messages are sent', 'paste remains a demo input')
        print('PASS bracketed paste accepted in isolated prompt')
    finally:
        try:
            send('\x11')
            wait_for(lambda: state_path.with_suffix('.exited').exists(), 'fixture process exits')
        finally:
            applescript('''on run argv
                tell application "Terminal"
                    if exists window id (item 1 of argv as integer) then close window id (item 1 of argv as integer)
                end tell
            end run''', window)
