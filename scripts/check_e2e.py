"""Opt-in live smoke: one tiny Codex turn in a disposable Git worktree."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_dashboard.client import ensure_service, request


def main():
    root = Path(tempfile.mkdtemp(prefix="cdx-live-"))
    repo, state = root / "repo", root / "state"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Dashboard Smoke", "-c", "user.email=smoke@example.invalid", "commit", "--allow-empty", "-m", "smoke fixture"], check=True, capture_output=True)
    ensure_service(state, "codex")
    pid = request(state, "snapshot")["pid"]
    thread_id = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            s = request(state, "snapshot")
            if s["connected"]:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(s.get("error") or "Connection timed out")
        task = request(state, "plan", repo=str(repo), title="Dashboard smoke test", prompt="Reply with exactly DASHBOARD_SMOKE_OK. Do not call tools, spawn agents, or modify files.")
        task = request(state, "start", taskId=task["id"])
        thread_id = task["threadId"]
        # The command client is disconnected for the entire turn.
        time.sleep(2)
        deadline = time.monotonic() + 90
        awake = False
        while time.monotonic() < deadline:
            s = request(state, "snapshot", threadId=thread_id)
            awake = awake or s["preventingSleep"]
            t = s["threads"][thread_id]
            if t.get("lastTurnStatus") in ("completed", "failed", "interrupted"):
                break
            if s["requests"]:
                raise RuntimeError("Smoke turn unexpectedly requested input")
            time.sleep(0.3)
        else:
            request(state, "interrupt", threadId=thread_id)
            raise RuntimeError("Live turn timed out")
        answers = [i["text"] for i in t["items"] if i["type"] == "agentMessage"]
        assert any("DASHBOARD_SMOKE_OK" in a for a in answers), t.get("activity")
        assert subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"]).strip() == b"main"
        assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]).strip()
        print(json.dumps({"result": "passed", "status": t["lastTurnStatus"], "isolatedWorktree": task["cwd"] != str(repo),
                          "clientDetached": True, "sleepAssertionObserved": awake, "threadId": thread_id, "artifacts": str(root)}, indent=2))
    finally:
        # Shut down only the disposable dashboard service, never Codex's shared daemon.
        os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
