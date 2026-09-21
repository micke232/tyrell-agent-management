"""Copilot's framed JSON-RPC transport and provider-owned agent sessions.

The connection monitor remains separate. Autonomy is an explicit per-agent setting.
"""
import asyncio
import contextlib
import json
import time
import uuid

from .connections import CopilotProbe, copilot_executable, normalize_host
from .rpc import RpcError
from .workspace_changes import changes


class CopilotRequestUncertain(RpcError):
    pass


class CopilotRuntime(CopilotProbe):
    def __init__(self, state, directory, expected_host, on_request):
        super().__init__()
        self.state, self.directory = state, directory
        self.expected_host, self.on_request = expected_host, on_request
        self.pending, self.sessions, self.tools = {}, {}, {}
        self.reader_task = None
        self.background = set()
        self.diff_jobs = {}
        self.diff_again = set()
        self.start_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()

    async def ensure(self):
        async with self.start_lock:
            if self.reader_task and not self.reader_task.done():
                return
            await self.close()
            executable = copilot_executable()
            if not executable:
                raise RpcError("Copilot CLI is not installed")
            await self.start(executable, str(self.directory))
            self.reader_task = asyncio.create_task(self.read())
            try:
                ping = await self.call("ping", {})
                if ping.get("protocolVersion") != 3:
                    raise RpcError("This Copilot integration requires CLI protocol 3")
                auth = await self.call("auth.getStatus", {})
                host = normalize_host(auth["host"]) if isinstance(auth.get("host"), str) else None
                if not auth.get("isAuthenticated") or (self.expected_host() and host != self.expected_host()):
                    raise RpcError("Sign in to Copilot using the configured GitHub host")
            except Exception:
                await self.close()
                raise

    async def send(self, message):
        async with self.write_lock:
            await super().send(message)

    async def call(self, method, params, timeout=30):
        self.sequence += 1
        rid = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise CopilotRequestUncertain("Copilot request timed out; inspect the agent before retrying")
        finally:
            self.pending.pop(rid, None)

    async def read(self):
        try:
            while True:
                header = await self.process.stdout.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise ValueError("Header too large")
                fields = dict(line.split(b":", 1) for line in header.strip().split(b"\r\n"))
                size = int(next(v for k, v in fields.items() if k.lower() == b"content-length"))
                if not 0 < size <= 4 * 1024 * 1024:
                    raise ValueError("Message too large")
                message = json.loads(await self.process.stdout.readexactly(size))
                if "method" in message:
                    await self.incoming(message)
                else:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RpcError("Copilot rejected the request (code %s)" % message["error"].get("code", "unknown")))
                        else:
                            future.set_result(message.get("result", {}))
        except (Exception, asyncio.CancelledError):
            if self.process and self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RpcError("Copilot disconnected; the prompt was not retried"))
            for tid in list(self.sessions):
                t = self.state.data["threads"].get(tid)
                if t and t.get("status", {}).get("type") == "active":
                    self.finish(tid, "interrupted")
            self.sessions.clear()

    async def incoming(self, message):
        method, p = message["method"], message.get("params", {})
        tid = "copilot-" + str(p.get("sessionId", ""))
        if tid not in self.sessions:
            if "id" in message:
                await self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unknown session"}})
            return
        if method == "session.event":
            self.event(tid, p.get("event", {}))
        elif method == "userInput.request" and "id" in message:
            rid = "copilot-input-" + str(message["id"])
            self.on_request({"id": rid, "provider": "copilot", "wireId": message["id"],
                             "method": "item/tool/requestUserInput", "params": {"threadId": tid,
                             "questions": [{"id": "answer", "header": "Copilot", "question": p.get("question", ""),
                                            "options": [{"label": c, "description": ""} for c in (p.get("choices") or [])]}]}})
        elif "id" in message:
            await self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unsupported client request"}})

    def emit(self, tid, method, **params):
        self.state.event(method, dict(threadId=tid, **params))

    def schedule(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def request_changes(self, tid):
        existing = self.diff_jobs.get(tid)
        if existing and not existing.done():
            self.diff_again.add(tid)
            return
        async def delayed():
            while True:
                self.diff_again.discard(tid)
                await asyncio.sleep(1)
                await self.collect_changes(tid)
                if tid not in self.diff_again:
                    break
        task = asyncio.create_task(delayed())
        self.diff_jobs[tid] = task
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def collect_changes(self, tid):
        t = self.state.thread(tid)
        try:
            records = await changes(t.get("setupCwd") or t["cwd"], t.get("agentWorktree", {}).get("baseCommit"))
            t.update(changedFiles=records, filesSource="git", filesBase=records.base,
                     filesError=("Showing the first 250 of %d changed files." % records.total) if records.truncated else None)
        except (OSError, ValueError, asyncio.TimeoutError):
            t["filesError"] = "Git diff unavailable or too large; reported file operations remain visible."
        self.state.dirty = True

    def finish(self, tid, status):
        t = self.state.thread(tid)
        if t.get("turnId"):
            self.emit(tid, "turn/completed", turn={"id": t["turnId"], "status": status})
            self.request_changes(tid)

    def event(self, tid, event):
        kind, data = event.get("type"), event.get("data", {})
        iid = data.get("messageId") or data.get("toolCallId") or event.get("id")
        if kind == "assistant.message_delta":
            self.emit(tid, "item/agentMessage/delta", itemId=iid, delta=data.get("deltaContent", ""))
        elif kind == "assistant.message":
            self.emit(tid, "item/completed", item={"id": iid, "type": "agentMessage", "text": data.get("content", "")})
        elif kind in ("tool.execution_start", "tool.execution_partial_result", "tool.execution_complete"):
            key = (tid, iid)
            if kind.endswith("start"):
                args = data.get("arguments") or {}
                self.tools[key] = {"name": data.get("toolName", "Copilot tool"),
                    "path": args.get("path") if isinstance(args, dict) else None,
                    "command": args.get("command") if isinstance(args, dict) else None, "output": ""}
            tool = self.tools.get(key, {"name": "Copilot tool", "output": ""})
            if kind.endswith("partial_result"):
                tool["output"] = (tool["output"] + data.get("partialOutput", ""))[-30000:]
            if kind.endswith("complete"):
                result = data.get("result") or {}
                content = result.get("content") if isinstance(result, dict) else None
                if isinstance(content, str):
                    tool["output"] = content[-30000:]
                elif data.get("error"):
                    tool["output"] = str(data["error"].get("message", "Tool failed"))[-30000:]
            status = "completed" if kind.endswith("complete") and data.get("success") else "failed" if kind.endswith("complete") else "running"
            if tool.get("command"):
                item = {"id": iid, "type": "commandExecution", "command": tool["command"], "aggregatedOutput": tool["output"], "status": status}
            else:
                item = {"id": iid, "type": "dynamicToolCall", "tool": tool["name"], "output": tool["output"], "status": status}
            self.emit(tid, "item/completed", item=item)
            if kind.endswith("complete"):
                self.tools.pop(key, None)
                path = tool.get("path")
                if path and tool["name"] in ("create", "edit", "str_replace_editor", "delete_file"):
                    self.emit(tid, "item/completed", item={"id": iid + "-file", "type": "fileChange", "status": status,
                              "changes": [{"path": path, "kind": {"type": "add" if tool["name"] == "create" else "delete" if tool["name"] == "delete_file" else "update"}}]})
                self.request_changes(tid)
        elif kind == "permission.requested" and not data.get("resolvedByHook"):
            permission = data.get("permissionRequest", {})
            rid = "copilot-permission-" + tid + "-" + data["requestId"]
            self.on_request({"id": rid, "provider": "copilot", "permissionId": data["requestId"],
                             "method": "item/commandExecution/requestApproval", "params": {
                                 "threadId": tid, "availableDecisions": ["accept", "cancel"],
                                 "command": permission.get("fullCommandText") or permission.get("path") or permission.get("kind", "Tool permission"),
                                 "reason": permission.get("intention") or "Copilot requests permission for this operation",
                                 "permission": permission}})
        elif kind == "permission.completed":
            self.state.requests.pop("copilot-permission-" + tid + "-" + str(data.get("requestId")), None)
            self.refresh_waiting(tid)
        elif kind == "session.idle":
            self.finish(tid, "completed")
        elif kind == "session.error":
            self.finish(tid, "failed")
            self.emit(tid, "item/completed", item={"id": str(uuid.uuid4()), "type": "agentMessage",
                      "text": "Copilot could not complete this turn. Check its connection and model access before retrying."})

    def refresh_waiting(self, tid):
        requests = [r for r in self.state.requests.values() if r.get("params", {}).get("threadId") == tid]
        t = self.state.thread(tid)
        if t.get("status", {}).get("type") == "active":
            t["status"]["activeFlags"] = (["waitingOnUserInput" if "requestUserInput" in requests[0]["method"] else "waitingOnApproval"] if requests else [])
        self.state.dirty = True

    async def respond(self, request, response):
        tid = request["params"]["threadId"]
        if "permissionId" in request:
            decision = response.get("decision")
            if decision not in ("accept", "cancel", "decline"):
                raise ValueError("Choose approve once or decline")
            result = await self.call("session.permissions.handlePendingPermissionRequest", {
                "sessionId": tid[8:], "requestId": request["permissionId"],
                "result": {"kind": "approve-once", "approvedInteractively": True} if decision == "accept" else {"kind": "reject"}})
            if result.get("success") is False:
                self.state.requests.pop(str(request["id"]), None)
                self.refresh_waiting(tid)
                raise ValueError("This Copilot request is no longer pending")
        else:
            answers = response.get("answers", {}).get("answer", {}).get("answers", [])
            if not answers:
                raise ValueError("Enter an answer")
            await self.send({"jsonrpc": "2.0", "id": request["wireId"], "result": {"answer": "\n".join(answers), "wasFreeform": True}})
        self.state.requests.pop(str(request["id"]), None)
        self.refresh_waiting(tid)
        return {}

    async def configure_access(self, t, config):
        autonomous = config.get("copilotAccess") == "Autonomous"
        await self.call("session.permissions.configure", {"sessionId": t["id"][8:],
            "approveAllToolPermissionRequests": autonomous, "approveAllReadPermissionRequests": autonomous,
            "paths": {"unrestricted": autonomous, "workspacePath": t.get("setupCwd") or t["cwd"]}})
        t["reportedAccess"] = {"provider": "copilot", "copilotAccess": config.get("copilotAccess", "Ask"),
                               "approvalPolicy": "Autonomous" if autonomous else "Ask"}
        self.state.dirty = True

    async def message(self, t, text, config, context, client_id=None):
        await self.ensure()
        tid = t["id"]
        cwd = t.get("setupCwd") or t["cwd"]
        signature = (cwd, config["model"])
        active = t.get("status", {}).get("type") == "active"
        if active and tid not in self.sessions:
            self.finish(tid, "interrupted")
            active = False
        if tid not in self.sessions or (not active and self.sessions[tid] != signature):
            if tid in self.sessions:
                await self.call("session.detach", {"sessionId": tid[8:]})
            params = {"sessionId": tid[8:], "workingDirectory": cwd, "streaming": True,
                      "requestPermission": True, "requestUserInput": True}
            if config["model"]:
                params["model"] = config["model"]
            self.sessions[tid] = signature
            try:
                result = await self.call("session.resume" if t.get("copilotCreated") else "session.create", params)
                if result.get("sessionId") != tid[8:]:
                    raise RpcError("Copilot returned a different session identifier")
                t["copilotCreated"] = True
                self.state.save()

            except Exception:
                self.sessions.pop(tid, None)
                raise
        if not active:
            await self.configure_access(t, config)
        # Tool events may arrive before session.send returns. Resolve their paths
        # against the actual session workspace from the beginning of the turn.
        t["cwd"] = cwd
        turn_id = t.get("turnId") or str(uuid.uuid4())
        if not active:
            self.emit(tid, "turn/started", turn={"id": turn_id})
        prompt = "Application instructions:\n" + "\n\n".join(v["value"] for v in context.values()) + "\n\nUser message:\n" + text
        item_id = client_id or str(uuid.uuid4())
        self.emit(tid, "item/completed", item={"id": item_id, "clientId": client_id, "type": "userMessage", "content": [{"text": text}]})
        try:
            result = await self.call("session.send", {"sessionId": tid[8:], "prompt": prompt,
                                     "displayPrompt": text, "mode": "immediate"})
        except CopilotRequestUncertain:
            t["activity"] = "Delivery unconfirmed; inspect or interrupt before retrying"
            raise
        except Exception:
            if not active:
                self.finish(tid, "failed")
            raise
        if not active:
            t["model"] = config["model"] or t.get("model")
        t["cwd"] = cwd
        self.state.dirty = True
        return result

    async def close(self):
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None
        self.sessions.clear()
        self.tools.clear()
        for task in list(self.background):
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.background.clear()
        self.diff_jobs.clear()
        self.diff_again.clear()
        await super().close()
