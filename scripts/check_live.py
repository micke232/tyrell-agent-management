"""Read-only smoke check of the real local Codex service, without printing chats."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tyrell.rpc import Rpc


async def main():
    notifications = []
    rpc = Rpc(["codex", "app-server", "proxy"], lambda method, p: notifications.append(method), lambda *_: None)
    try:
        await rpc.connect()
        models = await rpc.call("model/list", {})
        result = await rpc.call("thread/list", {"limit": 100, "sortKey": "updated_at", "sourceKinds": ["cli", "appServer", "exec", "vscode", "subAgent", "unknown"]})
        tid = "01a0b5b9-07d4-7b40-a851-5530472ecf71"
        read = await rpc.call("thread/read", {"threadId": tid, "includeTurns": True})
        resume = await rpc.call("thread/resume", {"threadId": tid})
        t = resume["thread"]
        print(json.dumps({"models": len(models["data"]), "threads": len(result["data"]),
                          "readTurns": len(read["thread"]["turns"]), "resumeTurns": len(t["turns"]),
                          "historyMode": t.get("historyMode"), "modelAvailable": bool(resume.get("model")),
                          "eventsReceived": len(notifications), "result": "passed"}, indent=2))
    finally:
        await rpc.close()


asyncio.run(main())
