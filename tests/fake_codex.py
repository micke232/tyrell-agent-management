#!/usr/bin/env python3
"""Deterministic app-server fixture. Never invokes a model or a real Codex process."""
import json
import sys
import threading
import time

lock = threading.Lock()
threads = {}
archived = set()
seq = 0


def emit(message):
    with lock:
        print(json.dumps(message), flush=True)


def event(method, **params):
    emit({"method": method, "params": params})


def complete(tid, turn):
    time.sleep(0.4)
    event("item/agentMessage/delta", threadId=tid, itemId="answer-" + turn["id"], delta="Background ")
    time.sleep(0.4)
    event("item/agentMessage/delta", threadId=tid, itemId="answer-" + turn["id"], delta="work completed")
    item = {"id": "answer-" + turn["id"], "type": "agentMessage", "text": "Background work completed"}
    turn["items"].append(item)
    event("item/completed", threadId=tid, item=item)
    turn["status"] = "completed"
    turn["durationMs"] = 800
    threads[tid]["status"] = {"type": "idle"}
    event("turn/completed", threadId=tid, turn=turn)


for line in sys.stdin:
    req = json.loads(line)
    if "method" not in req:
        continue
    method, p = req["method"], req.get("params", {})
    if "id" not in req:
        continue
    result = {}
    if method == "model/list":
        result = {"data": [{"id": "test-model", "model": "test-model", "displayName": "Test", "hidden": False,
                            "isDefault": True, "defaultReasoningEffort": "medium",
                            "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]}]}
    elif method == "thread/list":
        result = {"data": [dict(t, turns=[]) for t in threads.values() if (t["id"] in archived) == bool(p.get("archived"))]}
    elif method == "thread/archive":
        archived.add(p["threadId"])
        event("thread/archived", threadId=p["threadId"])
    elif method == "thread/unarchive":
        archived.discard(p["threadId"])
        event("thread/unarchived", threadId=p["threadId"])
        result = {"thread": threads[p["threadId"]]}
    elif method == "thread/name/set":
        threads[p["threadId"]]["name"] = p["name"]
    elif method == "thread/start":
        seq += 1
        tid = "thread-" + str(seq)
        t = {"id": tid, "cwd": p["cwd"], "status": {"type": "idle"}, "turns": [], "model": p.get("model", "test-model"), "canAcceptDirectInput": True}
        threads[tid] = t
        result = {"thread": t, "model": t["model"], "reasoningEffort": "medium"}
        event("thread/started", thread=t)
    elif method in ("thread/resume", "thread/read"):
        t = threads[p["threadId"]]
        result = {"thread": t, "model": t["model"], "reasoningEffort": "medium"}
    elif method == "turn/start":
        t = threads[p["threadId"]]
        seq += 1
        turn = {"id": "turn-" + str(seq), "status": "inProgress", "startedAt": time.time(), "items": []}
        t["turns"].append(turn)
        t["status"] = {"type": "active", "activeFlags": []}
        t["model"] = p.get("model", t["model"])
        event("turn/started", threadId=t["id"], turn=turn)
        if p.get("clientUserMessageId"):
            item = {"id": "user-" + str(seq), "clientId": p["clientUserMessageId"], "type": "userMessage", "content": p["input"]}
            turn["items"].append(item)
            event("item/completed", threadId=t["id"], item=item)
        event("turn/plan/updated", threadId=t["id"], turnId=turn["id"], plan=[{"step": "Do work", "status": "inProgress"}])
        result = {"turn": turn}
        threading.Thread(target=complete, args=(t["id"], turn), daemon=True).start()
    elif method == "turn/steer":
        if p["expectedTurnId"] != threads[p["threadId"]]["turns"][-1]["id"]:
            emit({"id": req["id"], "error": {"code": -1, "message": "Stale turn"}})
            continue
        if p.get("clientUserMessageId"):
            seq += 1
            item = {"id": "user-" + str(seq), "clientId": p["clientUserMessageId"], "type": "userMessage", "content": p["input"]}
            threads[p["threadId"]]["turns"][-1]["items"].append(item)
            event("item/completed", threadId=p["threadId"], item=item)
        result = {"turnId": p["expectedTurnId"]}
    elif method == "test/approval":
        emit({"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": p["threadId"], "command": "echo test", "reason": "test approval"}})
    emit({"id": req["id"], "result": result})
