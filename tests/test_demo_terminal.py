"""Exercise the real demo entry point and curses loop without agent services."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest


class DemoTerminalTests(unittest.TestCase):
    def test_demo_stays_alive_with_initial_planned_task_and_agent_navigation(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
            child = subprocess.Popen([sys.executable, '-B', str(root/'dashboard.py'), 'demo'],
                                     stdin=slave, stdout=slave, stderr=slave,
                                     env=dict(os.environ, TERM='xterm-256color', TYRELL_HOME=directory))
            output = bytearray()
            def drain(seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    if select.select([master], [], [], .05)[0]:
                        output.extend(os.read(master, 65536))
            try:
                drain(1)
                self.assertIsNone(child.poll(), output.decode(errors='replace'))
                self.assertIn(b'Add search filters', output)
                os.write(master, b'\t\x1b[B\x1b[A')
                drain(.3)
                self.assertIsNone(child.poll(), output.decode(errors='replace'))
                os.write(master, b'\x11')
                drain(.3)
                self.assertEqual(child.wait(timeout=3), 0, output.decode(errors='replace'))
                self.assertNotIn(b'Traceback', output)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                os.close(master)
                os.close(slave)
