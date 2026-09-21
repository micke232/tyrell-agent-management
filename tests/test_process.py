"""Exercise the actual daemon and Unix IPC across client detach and restart."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tyrell.client import request

ROOT = Path(__file__).resolve().parents[1]


class ProcessTests(unittest.TestCase):
    def test_daemon_reconnect_and_detached_client(self):
        with tempfile.TemporaryDirectory(prefix="cdx-ipc-") as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-b", "main")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
            # Run the real Service with a deterministic external JSONL app-server fixture.
            runner = root / "runner.py"
            runner.write_text("import asyncio,sys\nfrom pathlib import Path\nsys.path.insert(0," + repr(str(ROOT)) + ")\nfrom tyrell.service import Service\nasync def main():\n    await Service(sys.argv[1], [sys.executable, '-B', " + repr(str(ROOT / "tests/fake_codex.py")) + "]).run()\nasyncio.run(main())\n")
            state = root / "state"
            log = (root / "log").open("w")
            process = None
            def start():
                p = subprocess.Popen([sys.executable, "-B", str(runner), str(state)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        s = request(state, "snapshot")
                        if s["connected"]:
                            return p
                    except OSError:
                        pass
                    time.sleep(0.05)
                p.terminate()
                p.wait(timeout=5)
                raise AssertionError((root / "log").read_text())
            try:
                process = start()
                task = request(state, "plan", repo=str(repo), title="IPC smoke", prompt="work")
                task = request(state, "start", taskId=task["id"])
                # Every request() opens then closes its own socket. No UI remains connected.
                time.sleep(1.2)
                s = request(state, "snapshot", threadId=task["threadId"])
                self.assertEqual(s["tasks"][0]["status"], "completed")
                self.assertEqual(s["threads"][task["threadId"]]["items"][-1]["text"], "Background work completed")
                self.assertIsNone(process.poll())
                process.terminate()
                process.wait(timeout=5)
                # A planned task survives process restart, and is never auto-started.
                data = json.loads((state / "state.json").read_text())
                data["tasks"].append({"id": "later", "title": "Later", "repo": str(repo), "prompt": "later", "status": "planned"})
                (state / "state.json").write_text(json.dumps(data))
                # Remove managed fixture threads because the fake app server has no persistent store.
                for t in data["threads"].values():
                    t["managed"] = False
                (state / "state.json").write_text(json.dumps(data))
                process = start()
                s = request(state, "snapshot", threadId=task["threadId"])
                self.assertEqual(s["tasks"][-1]["status"], "planned")
                self.assertEqual(s["threads"][task["threadId"]]["items"][-1]["text"], "Background work completed")
                self.assertEqual(git("branch", "--show-current"), "main")
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                log.close()
