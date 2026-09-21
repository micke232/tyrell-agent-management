import json
import os
import time
from pathlib import Path

from .progress import message_plan, parse_estimate, plan_only_stop
from .files_view import record_changes


def item_text(item):
    kind = item.get("type", "")
    if kind == "userMessage":
        return "\n".join(c.get("text", "") for c in item.get("content", []))
    if kind in ("agentMessage", "plan"):
        return item.get("text", "")
    if kind == "reasoning":
        return "\n".join(item.get("summary", []))
    if kind == "commandExecution":
        return "$ " + item.get("command", "") + ("\n" + item["aggregatedOutput"] if item.get("aggregatedOutput") else "")
    if kind == "fileChange":
        return "Files: " + ", ".join(c.get("path", "") for c in item.get("changes", []))
    if kind in ("mcpToolCall", "dynamicToolCall", "collabAgentToolCall"):
        return item.get("tool", kind) + " · " + item.get("status", "") + ("\n" + item["output"] if item.get("output") else "")
    if kind == "webSearch":
        return "Search: " + item.get("query", "")
    return ""


def status_label(thread, connected=True, now=None):
    if not connected:
        return "offline"
    flags = thread.get("status", {}).get("activeFlags", [])
    if "waitingOnApproval" in flags:
        return "approval"
    if "waitingOnUserInput" in flags:
        return "question"
    kind = thread.get("status", {}).get("type", "notLoaded")
    if kind == "active":
        last = thread.get("lastActivity")
        if last and (now or time.time()) - last > 120:
            return "quiet (active)"
        return "working"
    if kind == "idle" and (thread.get("waitingAfterPlan") or plan_only_stop(thread, thread.get("items", []))):
        return "waiting"
    return {"notLoaded": "saved", "idle": "idle", "systemError": "error"}.get(kind, kind)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.json"
        self.data = {"version": 1, "threads": {}, "archived": {}, "hidden": [], "tasks": [], "settings": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))
        self.connected = False
        self.error = "Connecting to Codex…"
        self.models = []
        self.requests = {}
        self.dirty = False

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as f:
            os.chmod(temporary, 0o600)
            json.dump(self.data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(self.path)
        self.dirty = False

    def thread(self, tid, collection="threads"):
        return self.data[collection].setdefault(tid, {
            "id": tid, "name": tid[:8], "items": [], "plan": [],
            "status": {"type": "notLoaded"}, "lastActivity": None,
        })

    def merge_thread(self, info, history=False, collection="threads"):
        t = self.thread(info["id"], collection)
        old_status = t.get("status", {}).get("type")
        for key in ("cwd", "model", "reasoningEffort", "status", "updatedAt", "createdAt", "canAcceptDirectInput", "parentThreadId"):
            if key in info:
                t[key] = info[key]
        t["name"] = info.get("name") or info.get("agentNickname") or info.get("preview", "")[:90] or t["name"]
        if info.get("updatedAt"):
            t["lastActivity"] = max(t.get("lastActivity") or 0, info["updatedAt"])
        if history:
            turns = info.get("turns", [])
            if turns and t.get("planTurnId") != turns[-1]["id"]:
                t.update(plan=[], planExplanation=None, planSource=None, estimate=None)
            t["items"] = []
            t["changedFiles"] = {}
            for turn in info.get("turns", []):
                for item in turn.get("items", []):
                    self.put_item(t, item, read_plan=turn is turns[-1], historical=True)
                if turn is turns[-1] and t.get("planSource") == "message":
                    t["planTurnId"] = turn["id"]
                if turn.get("status") == "inProgress":
                    t["turnId"] = turn["id"]
                    t["startedAt"] = turn.get("startedAt") or t.get("startedAt") or time.time()
                else:
                    t["lastTurnStatus"] = turn.get("status")
                    t["lastCompletedTurnId"] = turn.get("id")
                    t["durationMs"] = turn.get("durationMs")
        if t.get("status", {}).get("type") != "active":
            t["turnId"] = None
            if old_status == "active":
                t["endedAt"] = time.time()
        self.dirty = True
        return t

    def put_item(self, thread, item, read_plan=True, historical=False):
        if item.get("type") == "fileChange":
            record_changes(thread, item)
        text = item_text(item)
        if read_plan and item.get("type") == "agentMessage" and thread.get("planSource") != "native":
            parsed = message_plan(text)
            if parsed:
                thread.update(plan=parsed[0], estimate=None if historical else parsed[1], planSource="message",
                              planTurnId=thread.get("turnId"))
        if not text and item.get("type") not in ("agentMessage", "reasoning"):
            return
        value = {"id": item.get("id"), "type": item.get("type"), "text": text[-30000:]}
        if item.get("clientId"):
            value["clientId"] = item["clientId"]
        items = thread.setdefault("items", [])
        for i, old in enumerate(items):
            if old["id"] == value["id"]:
                items[i] = value
                break
        else:
            items.append(value)
        thread["items"] = items[-250:]

    def event(self, method, p):
        if method == "thread/started":
            self.merge_thread(p["thread"])
            return
        tid = p.get("threadId")
        if not tid:
            return
        if method == "thread/archived":
            t = self.data["threads"].pop(tid, None)
            if t:
                self.data["archived"][tid] = t
            self.dirty = True
            return
        if method == "thread/unarchived":
            t = self.data["archived"].pop(tid, None)
            if t:
                self.data["threads"][tid] = t
            self.dirty = True
            return
        if method == "thread/deleted":
            self.data["threads"].pop(tid, None)
            self.data["archived"].pop(tid, None)
            self.dirty = True
            return
        if tid in self.data["archived"]:
            return
        t = self.thread(tid)
        self.dirty = True
        if method == "thread/status/changed":
            t["status"] = p["status"]
            if p["status"]["type"] != "active":
                t["turnId"] = None
            return
        if method == "serverRequest/resolved":
            self.requests.pop(str(p["requestId"]), None)
            return
        if method == "thread/name/updated":
            t["name"] = p.get("threadName") or p.get("name") or t["name"]
            return
        if method == "thread/settings/updated":
            settings = p.get("threadSettings", {})
            for key in ("model", "reasoningEffort"):
                if key in settings:
                    t[key] = settings[key]
            return
        if method == "turn/started":
            turn = p["turn"]
            t.update(turnId=turn["id"], startedAt=turn.get("startedAt") or time.time(), endedAt=None,
                     status={"type": "active", "activeFlags": []}, activity="Working", plan=[],
                     planTurnId=turn["id"], planExplanation=None, planSource=None, estimate=None, lastTurnStatus=None)
        elif method == "turn/completed":
            turn = p["turn"]
            t.update(turnId=None, endedAt=turn.get("completedAt") or time.time(),
                     durationMs=turn.get("durationMs"), lastTurnStatus=turn["status"],
                     lastCompletedTurnId=turn["id"],
                     status={"type": "systemError" if turn["status"] == "failed" else "idle"},
                     activity=turn["status"])
            if turn.get("error"):
                t["activity"] = turn["error"].get("message", "Turn failed")
            for key in list(self.requests):
                if self.requests[key]["params"].get("threadId") == tid:
                    del self.requests[key]
            for task in self.data["tasks"]:
                if task.get("threadId") == tid and task["status"] == "running":
                    task["status"] = {"completed": "completed", "failed": "failed", "interrupted": "interrupted"}.get(turn["status"], "interrupted")
        elif method == "turn/plan/updated":
            t["plan"] = p["plan"]
            t["planSource"] = "native"
            t["planTurnId"] = p.get("turnId") or t.get("turnId")
            t["planExplanation"] = p.get("explanation")
            t["estimate"] = parse_estimate(p.get("explanation"))
        elif method in ("item/started", "item/completed"):
            self.put_item(t, p["item"])
            t["activity"] = (item_text(p["item"]).split("\n")[0] or p["item"].get("type", "Working"))[:160]
        elif method in ("item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/plan/delta", "item/commandExecution/outputDelta"):
            iid = p["itemId"]
            item = next((i for i in t["items"] if i["id"] == iid), None)
            if item is None:
                item = {"id": iid, "type": "agentMessage" if "agentMessage" in method else "reasoning", "text": ""}
                t["items"].append(item)
            item["text"] = (item["text"] + p.get("delta", ""))[-30000:]
            t["activity"] = "Responding" if "agentMessage" in method else "Working"
        elif method == "error":
            t["activity"] = p.get("error", {}).get("message", "Codex error")
        else:
            return
        t["lastActivity"] = time.time()

    def snapshot(self):
        return {**self.data, "connected": self.connected, "error": self.error,
                "models": self.models, "requests": list(self.requests.values())}
