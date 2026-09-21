"""Isolated real-PTY startup/input/reply checks; never connects to real agents."""
import copy
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def check():
    with tempfile.TemporaryDirectory(prefix='tyrell-perf-', dir='/tmp') as directory:
        root = Path(directory)
        stop = threading.Event()
        lock = threading.Lock()
        thread = {'id': 'fixture', 'name': 'Performance fixture', 'status': {'type': 'idle'},
                  'items': [{'id': str(i), 'type': 'agentMessage', 'text': ('Fixture response åäö 猫.\n' * 10)} for i in range(120)]}
        snapshot = {'connected': True, 'threads': {'fixture': thread}, 'tasks': [], 'requests': [],
                    'providers': {'codex': {'connected': True, 'status': 'connected'},
                                  'copilot': {'connected': True, 'status': 'connected'}}}
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(root/'service.sock'))
        server.listen()
        server.settimeout(.1)

        def serve():
            while not stop.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection, connection.makefile('rb') as reader:
                    request = json.loads(reader.readline())
                    with lock:
                        response = snapshot if request['action'] == 'snapshot' else {}
                        data = json.dumps({'result': response}).encode() + b'\n'
                    try:
                        connection.sendall(data)
                    except BrokenPipeError:
                        pass
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 60, 240, 0, 0))
        env = dict(os.environ, TERM='xterm-256color')
        env.pop('NO_COLOR', None)
        child = subprocess.Popen([sys.executable, '-B', str(ROOT/'tests/performance_terminal_fixture.py'), directory],
                                 stdin=slave, stdout=slave, stderr=slave, env=env)
        original_terminal = termios.tcgetattr(slave)
        output = bytearray()

        def drain():
            while True:
                try:
                    part = os.read(master, 65536)
                except OSError:
                    break
                if not part:
                    break
                output.extend(part)
        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()

        def frame():
            path = root/'frame.json'
            return json.loads(path.read_text()) if path.exists() else {}

        def wait(predicate, label, timeout=8):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if predicate():
                    return
                if child.poll() is not None:
                    raise AssertionError('UI exited early: ' + output.decode(errors='replace')[-1500:])
                time.sleep(.005)
            raise AssertionError('Timed out: ' + label)

        try:
            wait(lambda: b'Press any key to continue' in output, 'animated startup')
            assert b'\x1b[38;5;108m' in output
            assert b'INTERFACE 2037\nREADY FOR INQUIRY' in output.replace(b'\r', b'')
            time.sleep(.15)
            assert not (root/'frame.json').exists(), 'Startup must wait for a key'
            os.write(master, b' ')
            wait(lambda: frame().get('renders', 0) > 0, 'dashboard first frame')
            time.sleep(.8)
            first = frame()['renders']
            time.sleep(1.1)
            idle_renders = frame()['renders'] - first
            assert idle_renders <= 1, ('Redundant idle repaints', idle_renders)
            draft, inputs, replies = '', [], []
            for i in range(8):
                char = chr(ord('a') + i)
                draft += char
                start = time.monotonic()
                # Motion bursts compete with typing and incoming responses.
                os.write(master, b'\x1b[<35;45;20M' * 80 + char.encode())
                wait(lambda: frame().get('buffer') == draft, 'prompt input')
                inputs.append((time.monotonic() - start) * 1000)
                marker = 'REPLY_%03d_VISIBLE' % i
                with lock:
                    thread['status'] = {'type': 'active', 'activeFlags': []}
                    thread['items'].append({'id': 'fresh-'+str(i), 'type': 'agentMessage', 'text': marker})
                start = time.monotonic()
                wait(lambda: marker in frame().get('text', []), 'incoming reply')
                replies.append((time.monotonic() - start) * 1000)
            fixed_statuses = frame()['statusPairs']
            os.write(master, b'\x1b[21~')
            wait(lambda: frame().get('panel') == 'HUB SETTINGS' and frame().get('settingsMarker') == '›', 'visible F10 selection')
            for selected in ('m', 'k', 'g', 'c'):
                os.write(master, b'\x1b[B')
                wait(lambda: frame().get('settingsSelected') == selected and frame().get('settingsMarker') == '›', 'visible arrow navigation: ' + selected)
            os.write(master, b'\r')
            wait(lambda: frame().get('panel') == 'APPEARANCE', 'F10 custom colors')
            os.write(master, b'\r')
            wait(lambda: frame().get('picking'), '256-color picker')
            os.write(master, b'h#5fd7ff\r')
            wait(lambda: frame().get('appearance', {}).get('base') == [81, 234], 'save chosen text color')
            assert frame()['basePair'] == [81, 234]
            assert frame()['statusPairs'] == fixed_statuses
            assert json.loads((root/'appearance.json').read_text()) == {'base': [81, 234]}
            os.write(master, b'd')
            wait(lambda: frame().get('appearance') == {} and frame().get('basePair') == [252, 234], 'restore default colors')
            assert frame()['statusPairs'] == fixed_statuses
            assert frame()['buffer'] == draft
            print('PASS real curses color picker, saved color pair, reset and unchanged status colors')
            assert max(inputs) < 350, inputs
            assert max(replies) < 900, replies
            print(json.dumps({'startup': 'animated, green, waits for key', 'idle_renders_in_1_1s': idle_renders,
                              'input_under_mouse_burst_ms': {'median': round(statistics.median(inputs), 2), 'max': round(max(inputs), 2)},
                              'snapshot_to_visible_reply_ms': {'median': round(statistics.median(replies), 2), 'max': round(max(replies), 2)}}, indent=2))
        finally:
            if child.poll() is None:
                os.write(master, b'\x11')
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
            restored = termios.tcgetattr(slave)
            # macOS can set PENDIN when tcsetattr restores canonical input. It is
            # transient kernel bookkeeping, not a leaked raw/echo/flow-control mode.
            restored[3] &= ~getattr(termios, 'PENDIN', 0)
            original_terminal[3] &= ~getattr(termios, 'PENDIN', 0)
            assert restored == original_terminal, 'Terminal modes were not restored'
            os.close(slave)
            os.close(master)
            stop.set()
            worker.join(timeout=1)
            server.close()


if __name__ == '__main__':
    check()
