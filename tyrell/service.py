import asyncio
import contextlib
import fcntl
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path

from .rpc import Rpc, RpcError
from . import __version__
from .hub_settings import local_report
from .state import State
from .progress import PROGRESS_CONTEXT, INTERVENTION_CONTEXT, plan_only_stop
from .worktrees import create_worktree, git
from .connections import CopilotConnection, normalize_host
from .processes import inventory, choose_port
from .handoff import prepare as prepare_handoff
from .workspace_changes import git_bytes, changes as workspace_changes
from .files_view import workspace_root
from urllib.parse import urlparse
from .copilot_runtime import CopilotRuntime
from .agent_setup import (discover_project, effective_config, instruction_text, model_info,
                          discover_git, target_entity, validate_engine, validate_patch, guidance_conflicts, project_root)


SOURCE_KINDS = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]


class Service:
    def __init__(self, directory, command):
        self.state = State(directory)
        self.directory = Path(directory)
        self.command = command
        self.rpc = None
        self.subscribed = set()
        self.observed = set()
        self.selected = set()
        self.locks = {}
        self.caffeine = None
        self.sleep_error = None
        self.stop = None
        self.copilot = CopilotConnection()
        self.copilot_runtime = CopilotRuntime(self.state, self.directory,
            lambda: self.state.data["settings"].get("copilotHost"), self.on_request)
        self.process_inventory = {}
        self.installation = {}
        self.workspace_git = {}
        self.workspace_requested = None
        self.files_requested = None
        self.setup_requested = None
        self.files_browser = None
        self.process_requested_at = 0

    def is_copilot(self, tid):
        return (self.state.data["threads"].get(tid) or self.state.data["archived"].get(tid) or {}).get("provider") == "copilot"

    def models_for(self, entity):
        if entity.get("provider") == "copilot":
            return [{"model": m["id"], "displayName": m["name"], "supportedReasoningEfforts": []}
                    for m in self.copilot.status.get("models", [])]
        return self.state.models

    def clear_codex_requests(self):
        for key, request in list(self.state.requests.items()):
            if request.get("provider") != "copilot":
                self.state.requests.pop(key, None)

    def providers(self):
        return {
            "codex": {"name": "Codex", "connected": self.state.connected,
                      "status": "connected" if self.state.connected else "connecting",
                      "models": [{"id": m["model"], "name": m.get("displayName") or m["model"]}
                                 for m in self.state.models] if self.state.connected else []},
            "copilot": {**self.copilot.status, "expectedHost": self.state.data["settings"].get("copilotHost"),
                        "repositoryVerification": self.state.data["settings"].get("repositoryVerification")},
        }

    def on_request(self, request):
        self.state.requests[str(request["id"])] = request
        tid = request.get("params", {}).get("threadId")
        if tid:
            t = self.state.thread(tid)
            flag = "waitingOnUserInput" if "requestUserInput" in request["method"] else "waitingOnApproval"
            t["status"] = {"type": "active", "activeFlags": [flag]}

    async def pages(self, method, params):
        values = []
        while True:
            result = await self.rpc.call(method, params)
            values.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return values
            params = {**params, "cursor": cursor}

    async def refresh(self):
        async with self.locks.setdefault("catalog", asyncio.Lock()):
            await self.refresh_unlocked()

    async def refresh_unlocked(self):
        threads = await self.pages("thread/list", {"limit": 100, "sortKey": "updated_at", "sourceKinds": SOURCE_KINDS})
        for info in threads:
            self.state.data["archived"].pop(info["id"], None)
            self.state.merge_thread(info)
        for tid, t in list(self.state.data["threads"].items()):
            if (t.get("managed") or tid in self.selected or t.get("status", {}).get("type") == "active") and tid not in self.subscribed:
                try:
                    await self.subscribe(tid)
                except RpcError as error:
                    t["activity"] = str(error)
        for task in self.state.data["tasks"]:
            if task["status"] == "running":
                t = self.state.data["threads"].get(task.get("threadId"), {})
                if t.get("status", {}).get("type") != "active" and t.get("lastTurnStatus"):
                    task["status"] = t["lastTurnStatus"]

    async def archives(self):
        infos = await self.pages("thread/list", {"archived": True, "limit": 100, "sortKey": "updated_at", "sourceKinds": SOURCE_KINDS})
        ids = {info["id"] for info in infos}
        for tid in list(self.state.data["archived"]):
            if tid not in ids and not self.is_copilot(tid):
                self.state.data["archived"].pop(tid, None)
        for info in infos:
            tid = info["id"]
            old = self.state.data["threads"].pop(tid, None)
            if old:
                self.state.data["archived"][tid] = old
            self.state.merge_thread(info, collection="archived")
            self.subscribed.discard(tid)
            self.selected.discard(tid)
        self.state.save()
        return {"archived": self.state.data["archived"]}

    async def subscribe(self, tid):
        if self.is_copilot(tid):
            self.subscribed.add(tid)
            return self.state.thread(tid)
        if tid in self.observed:
            result = await self.rpc.call("thread/read", {"threadId": tid, "includeTurns": True})
            t = self.state.merge_thread(result["thread"], history=True)
            t["externalWriter"] = True
            return t
        try:
            result = await self.rpc.call("thread/resume", {"threadId": tid})
        except RpcError as error:
            if "already has an active writer" not in str(error).lower():
                raise
            result = await self.rpc.call("thread/read", {"threadId": tid, "includeTurns": True})
            t = self.state.merge_thread(result["thread"], history=True)
            self.observed.add(tid)
            t["externalWriter"] = True
            return t
        self.state.thread(tid).pop("externalWriter", None)
        resumed = self.state.merge_thread(result["thread"], history=True)
        resumed["reportedAccess"] = {"sandbox": result.get("sandbox"), "approvalPolicy": result.get("approvalPolicy")}
        t = self.state.thread(tid)
        t["model"] = result.get("model", t.get("model"))
        t["reasoningEffort"] = result.get("reasoningEffort", t.get("reasoningEffort"))
        self.subscribed.add(tid)
        return t

    def codex_event(self, method, params):
        self.state.event(method, params)
        if method == "turn/completed" and params.get("threadId"):
            self.copilot_runtime.request_changes(params["threadId"])

    async def monitor_files(self):
        while not self.stop.is_set():
            requested = self.files_requested
            if requested and time.monotonic() - requested[2] < 10:
                tid, root, _ = requested
                if root:
                    try:
                        result = await self.dispatch({"action": "files_open", "path": root})
                        self.files_browser = {**result, "threadId": tid}
                    except (OSError, ValueError) as error:
                        self.files_browser = {"root": root, "records": {}, "threadId": tid, "error": str(error)}
                elif tid in self.state.data["threads"]:
                    self.copilot_runtime.request_changes(tid)
            await asyncio.sleep(3)

    async def connection_loop(self):
        while not self.stop.is_set():
            self.rpc = Rpc(self.command, self.codex_event, self.on_request)
            try:
                await self.rpc.connect()
                self.subscribed.clear()
                self.observed.clear()
                self.clear_codex_requests()
                self.state.models = await self.pages("model/list", {})
                await self.refresh()
                self.state.connected = True
                self.state.error = None
                while not self.rpc.reader_task.done() and not self.stop.is_set():
                    await asyncio.sleep(5)
                    await self.refresh()
                if self.rpc.reader_task.done():
                    self.rpc.reader_task.result()
                    raise RpcError("Codex proxy disconnected. " + self.rpc.stderr[-500:])
            except (OSError, ValueError, RpcError, asyncio.TimeoutError) as error:
                self.state.error = str(error)
            finally:
                self.state.connected = False
                self.clear_codex_requests()
                await self.rpc.close()
            try:
                await asyncio.wait_for(self.stop.wait(), 3)
            except asyncio.TimeoutError:
                pass

    async def monitor_workspace(self):
        while not self.stop.is_set():
            cwd = self.workspace_requested
            if cwd:
                try:
                    values = (await git_bytes(cwd, 'rev-parse', '--abbrev-ref', 'HEAD', '--absolute-git-dir', '--git-common-dir')).decode().splitlines()
                    branch, git_dir, common_dir = values
                    commit = (await git_bytes(cwd, "rev-parse", "--short", "HEAD")).decode().strip()
                    common = (Path(cwd) / common_dir).resolve()
                    self.workspace_git = {cwd: {"branch": None if branch == "HEAD" else branch,
                                                "commit": commit, "worktree": Path(git_dir).resolve() != common}}
                except (OSError, ValueError):
                    self.workspace_git = {cwd: {"unavailable": True}}
            if self.setup_requested and time.monotonic() - self.setup_requested[1] < 10:
                await self.refresh_setup_git(self.setup_requested[0])
            await asyncio.sleep(3)

    async def refresh_setup_git(self, root):
        detected = await asyncio.to_thread(discover_git, Path(root))
        profiles = self.state.data.setdefault("projectProfiles", {})
        profile = profiles.setdefault(root, {"path": root, "name": Path(root).name, "config": {}})
        if any(profile.get(key) != value for key, value in detected.items()):
            profile.update(detected)
            self.state.dirty = True

    async def check_installation(self):
        self.installation = await asyncio.to_thread(local_report, self.command[0])

    async def maintain(self):
        while not self.stop.is_set():
            if self.state.dirty:
                self.state.save()
            # Keep an existing sleep assertion across a transient connection loss.
            active = any(t.get("status", {}).get("type") == "active" for t in self.state.data["threads"].values())
            active = active and self.state.data["settings"].get("keepAwake", True)
            if sys.platform == "darwin":
                if active and (self.caffeine is None or self.caffeine.returncode is not None):
                    try:
                        self.caffeine = await asyncio.create_subprocess_exec("/usr/bin/caffeinate", "-i", "-w", str(os.getpid()))
                        self.sleep_error = None
                    except OSError as error:
                        self.sleep_error = str(error)
                elif not active and self.caffeine and self.caffeine.returncode is None:
                    self.caffeine.terminate()
                    await self.caffeine.wait()
                    self.caffeine = None
            await asyncio.sleep(1)

    async def monitor_processes(self):
        while not self.stop.is_set():
            if time.monotonic() - self.process_requested_at < 10:
                try:
                    self.process_inventory = await inventory(dict(self.state.data["threads"]))
                except (OSError, asyncio.TimeoutError):
                    self.process_inventory = {"rows": [], "error": "Process inventory unavailable", "checkedAt": time.time()}
            await asyncio.sleep(5)

    def validate_model(self, model, effort):
        if not model:
            if effort:
                raise ValueError("Select a model before selecting its reasoning effort")
            return
        found = next((m for m in self.state.models if m["model"] == model), None)
        if not found:
            raise ValueError("Choose a model from /models")
        if effort and effort not in [e["reasoningEffort"] for e in found["supportedReasoningEfforts"]]:
            raise ValueError("This model does not advertise that reasoning effort")

    async def dispatch(self, req):
        action = req.get("action")
        if action == "diagnostics":
            self.installation = await asyncio.to_thread(local_report, self.command[0])
            return self.installation
        if action == "connections":
            return self.providers()
        if action == "verify_repository":
            t = self.state.data["threads"].get(req.get("threadId"), {})
            cwd = project_root(t) if t else req.get("path")
            if not cwd:
                raise ValueError("Choose a Git repository in Setup first")
            remote = (await git_bytes(cwd, "remote", "get-url", "origin")).decode().strip()
            parsed = urlparse(remote)
            host = self.state.data["settings"].get("copilotHost")
            if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname or "https://" + parsed.hostname != host:
                raise ValueError("Verification requires an HTTPS origin on the configured Copilot GitHub host")
            output = await git_bytes(cwd, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "ls-remote", "--exit-code", remote, "HEAD")
            if not output.strip().endswith(b"HEAD"):
                raise ValueError("Remote HEAD was not readable")
            verification = {"host": host, "repository": parsed.path.lstrip("/").removesuffix(".git"), "checkedAt": time.time(), "method": "Git HTTPS", "readable": True}
            self.state.data["settings"]["repositoryVerification"] = verification
            self.state.save()
            return verification
        if action == "app_settings":
            patch = req.get("patch", {})
            if not patch or any(k not in ("mouseEnabled", "keepAwake") or type(v) is not bool for k, v in patch.items()):
                raise ValueError("Invalid application settings")
            self.state.data["settings"].update(patch)
            self.state.save()
            return patch
        if action == "copilot_host":
            if any(t.get("provider") == "copilot" and t.get("status", {}).get("type") == "active" for t in self.state.data["threads"].values()):
                raise ValueError("Wait until Copilot agents are ready before changing GitHub host")
            await self.copilot_runtime.close()
            self.state.data["settings"]["copilotHost"] = normalize_host(req["host"])
            self.copilot.update("connecting")
            self.state.save()
            return {}
        if action == "snapshot":
            entity = next((self.state.data.get(bucket, {}).get(req.get("threadId")) for bucket in ("threads", "archived")
                           if self.state.data.get(bucket, {}).get(req.get("threadId"))), None)
            if entity:
                self.workspace_requested = workspace_root(entity)
            if req.get("view") == "setup" and entity:
                self.setup_requested = (project_root(entity), time.monotonic())
            if req.get("view") == "files" and entity:
                self.files_requested = (entity["id"], req.get("filesRoot"), time.monotonic())
            if req.get("view") == "processes":
                self.process_requested_at = time.monotonic()
            snapshot = self.state.snapshot()
            hidden = set(self.state.data["hidden"])
            snapshot["hiddenThreads"] = {tid: t for tid, t in snapshot["threads"].items() if tid in hidden}
            snapshot["threads"] = {tid: t for tid, t in snapshot["threads"].items() if tid not in hidden}
            for collection in ("threads", "archived", "hiddenThreads"):
                snapshot[collection] = {tid: {**t, "waitingAfterPlan": bool(plan_only_stop(t, t.get("items", [])))}
                                        for tid, t in snapshot[collection].items()}
            if not req.get("full"):
                for collection in ("threads", "archived", "hiddenThreads"):
                    snapshot[collection] = {tid: {**t, "items": t.get("items", []) if tid == req.get("threadId") else [],
                                                 "changedFiles": t.get("changedFiles", {}) if tid == req.get("threadId") else {}}
                                            for tid, t in snapshot[collection].items()}
            return {**snapshot, "apiVersion": 9, "appVersion": __version__, "installation": self.installation, "workspaceGit": self.workspace_git, "filesBrowser": self.files_browser if req.get("view") == "files" else None, "pid": os.getpid(), "providers": self.providers(), "processInventory": self.process_inventory,
                    "preventingSleep": bool(self.caffeine and self.caffeine.returncode is None), "sleepError": self.sleep_error}
        if action == "files_open":
            root = Path(req["path"]).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Choose a folder")
            try:
                records = await workspace_changes(root)
                error = ("Showing the first 250 of %d changed files." % records.total) if records.truncated else ""
                base = records.base
                records = {path: record for path, record in records.items()
                           if Path(path).is_relative_to(root)}
            except (ValueError, OSError) as exc:
                base = None
                records, error = {}, "Git changes unavailable for this folder: " + str(exc)
            return {"root": str(root), "records": records, "error": error, "base": base}
        if action == "setup_import":
            profile = await asyncio.to_thread(discover_project, req["path"])
            profiles = self.state.data.setdefault("projectProfiles", {})
            existing = profiles.get(profile["path"], {})
            for key in ("instructionConflicts", "wikiGuidance"):
                if key in existing:
                    profile[key] = existing[key]
            previous = existing.get("detectedConfig", {})
            profile["config"].update({key: value for key, value in existing.get("config", {}).items()
                                      if key not in previous or value != previous[key]})
            target = req.get("target")
            if target:
                entity = target_entity(self.state.data, target)
                if not entity:
                    raise ValueError("Select an existing agent")
                if isinstance(entity.get("status"), dict) and entity["status"].get("type") == "active":
                    raise ValueError("Wait until the agent is ready before changing its folder")
                worktree = entity.get("agentWorktree")
                if worktree and entity.get("projectRoot") != profile["path"]:
                    raise ValueError("This agent already has a worktree. Create a new agent for a different project; its current work is preserved.")
                entity["projectRoot"] = profile["path"]
                entity["setupCwd"] = worktree["cwd"] if worktree else profile["path"]
            profiles[profile["path"]] = profile
            self.state.data["setupRevision"] = self.state.data.get("setupRevision", 0) + 1
            self.state.save()
            return {"profile": profile, "target": req.get("target"), "revision": self.state.data["setupRevision"]}
        if action == "agent_setup":
            scope = req.get("scope")
            if scope == "defaults":
                owner = self.state.data["settings"]
            elif scope == "project":
                owner = self.state.data.get("projectProfiles", {}).get(req.get("path"))
            elif scope == "agent":
                owner = target_entity(self.state.data, req.get("target"))
            else:
                raise ValueError("Select a setup scope")
            if owner is None or (scope == "agent" and not owner):
                raise ValueError("Select an existing agent, task or project profile")
            key = "config" if scope == "project" else "agentConfig"
            patch = validate_patch(req.get("patch", {}))
            if owner.get("provider") == "copilot" and set(patch) & {"fileAccess", "approvalMode", "networkAccess", "effort", "tier"}:
                raise ValueError("Copilot uses its CLI permission policy and model defaults; Codex sandbox and speed settings do not apply")
            if scope == "agent" and owner.get("agentWorktree") and set(patch) & {"useWorktree", "baseBranch"}:
                raise ValueError("This agent already has a worktree. Create another agent to use a different base or workspace mode.")
            config = {} if req.get("reset") else {**owner.get(key, {}), **patch}
            # Validate against the effective model, then commit the entire edit atomically.
            from copy import deepcopy
            trial = deepcopy(self.state.data)
            trial_owner = (trial["settings"] if scope == "defaults" else
                           trial["projectProfiles"][req["path"]] if scope == "project" else target_entity(trial, req["target"]))
            trial_owner[key] = config
            if set(patch) & {"model", "effort", "tier"}:
                effective = effective_config(trial, req.get("target") if scope == "agent" else None,
                                             req.get("path") if scope == "project" else None, scope == "defaults")
                validate_engine(effective, self.models_for(owner), owner)
            live_access = (scope == "agent" and owner.get("provider") == "copilot"
                           and owner.get("id") in self.copilot_runtime.sessions
                           and ("copilotAccess" in patch or req.get("reset")))
            if live_access:
                await self.copilot_runtime.configure_access(owner, effective_config(trial, req["target"]))
            owner[key] = config
            if req.get("reset") and scope != "project":
                owner.pop("nextModel", None)
                owner.pop("nextEffort", None)
            self.state.data["setupRevision"] = self.state.data.get("setupRevision", 0) + 1
            self.state.save()
            if live_access and owner.get("reportedAccess", {}).get("copilotAccess") == "Autonomous":
                for pending in list(self.state.requests.values()):
                    if (pending.get("provider") == "copilot" and pending.get("permissionId")
                            and pending.get("params", {}).get("threadId") == owner["id"]):
                        try:
                            await self.copilot_runtime.respond(pending, {"decision": "accept"})
                        except ValueError:
                            pass  # The CLI may already have resolved the request after reconfiguration.
            return {"scope": scope, "target": req.get("target"), "path": req.get("path"),
                    "config": config, "reset": bool(req.get("reset")), "revision": self.state.data["setupRevision"]}
        if action == "remove":
            tid = req["threadId"]
            if tid not in self.state.data["threads"]:
                raise ValueError("Select an agent in the sidebar first")
            if tid not in self.state.data["hidden"]:
                self.state.data["hidden"].append(tid)
            self.selected.discard(tid)
            self.state.save()
            return {"threadId": tid}
        if action == "restore" and req["threadId"] in self.state.data["hidden"] and req["threadId"] not in self.state.data["archived"]:
            self.state.data["hidden"].remove(req["threadId"])
            self.state.save()
            return {"threadId": req["threadId"]}
        if action == "plan":
            title, prompt = req["title"].strip(), req["prompt"].strip()
            if not title or not prompt:
                raise ValueError("A title and prompt are required")
            repo = str(Path(req["repo"]).expanduser().resolve())
            if not Path(repo).is_dir():
                raise ValueError("Project directory does not exist")
            task = {"id": str(uuid.uuid4()), "title": title, "prompt": prompt, "repo": repo, "status": "planned"}
            self.state.data["tasks"].append(task)
            self.state.save()
            return task
        if action == "handoff":
            tid = req["threadId"]
            async with self.locks.setdefault(tid, asyncio.Lock()):
                source = self.state.data["threads"].get(tid)
                if not source or source.get("status", {}).get("type") == "active":
                    raise ValueError("Wait until the source agent is ready, or interrupt it before handing over")
                if source.get("provider") != "copilot":
                    if not self.state.connected:
                        raise ValueError("Reconnect Codex so the source agent's status can be verified")
                    current = await self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})
                    if current["thread"].get("status", {}).get("type") == "active":
                        raise ValueError("The source agent is still active; wait or interrupt before handing over")
                provider = req.get("provider")
                if provider not in ("codex", "copilot"):
                    raise ValueError("Choose a provider")
                model = req.get("model") or ""
                validate_engine({"model": model, "effort": "", "tier": ""}, self.models_for({"provider": provider}))
                if not self.providers()[provider].get("connected"):
                    raise ValueError("The target provider is not connected")
                # Create the catalog entry first so a partially prepared workspace
                # remains discoverable if preparation fails.
                result = await self.dispatch({"action": "create_agent", "name": req.get("name"), "provider": provider, "model": model})
                target = self.state.thread(result["threadId"])
                target.update(handoffPreparing=True, status={"type": "active"}, activity="Preparing handoff")
                try:
                    workspace, context, omitted = await prepare_handoff(source, self.directory)
                    target.update(agentWorktree=workspace, setupCwd=workspace["cwd"], cwd=workspace["cwd"],
                                  projectRoot=project_root(source), handoffFrom=tid, handoffContext=context,
                                  handoffOmitted=omitted, handoffPreparing=False, status={"type": "idle"}, activity="Handoff prepared; send a message to begin")
                    config = effective_config(self.state.data, "thread:" + tid)
                    config.update(model=model, effort="", tier="", fileAccess="Keep current", approvalMode="Keep current", copilotAccess="Ask")
                    target["agentConfig"] = config
                    source.setdefault("handoffs", []).append(target["id"])
                    self.state.save()
                    return dict(result, preview=context, omitted=omitted)
                except Exception:
                    target.update(handoffPreparing=False, status={"type": "idle"})
                    target["activity"] = "Handoff preparation failed; choose a folder or remove this empty agent"
                    self.state.save()
                    raise
        if action == "settings":
            target = self.state.thread(req["threadId"]) if req.get("threadId") else self.state.data["settings"]
            validate_engine({"model": req.get("model") or "", "effort": req.get("effort") or "", "tier": ""}, self.models_for(target), target)
            target["nextModel"], target["nextEffort"] = req.get("model"), req.get("effort")
            target.setdefault("agentConfig", {}).update(model=req.get("model") or "", effort=req.get("effort") or "", tier="")
            self.state.data["setupRevision"] = self.state.data.get("setupRevision", 0) + 1
            self.state.save()
            return {}
        tid = req.get("threadId")
        copilot_action = self.is_copilot(tid) or (action == "create_agent" and req.get("provider") == "copilot")
        if action == "respond":
            pending = self.state.requests.get(str(req.get("requestId")))
            if pending and pending.get("provider") == "copilot":
                return await self.copilot_runtime.respond(pending, req["response"])
        if copilot_action and action in ("rename", "archive", "restore", "read_archive", "interrupt"):
            async with self.locks.setdefault("catalog", asyncio.Lock()):
                async with self.locks.setdefault(tid, asyncio.Lock()):
                    collection = "archived" if tid in self.state.data["archived"] else "threads"
                    t = self.state.data[collection][tid]
                    if action == "rename":
                        name = req["name"].strip()
                        if not name or len(name) > 100 or not all(c.isprintable() for c in name):
                            raise ValueError("Enter a name of 1–100 characters")
                        t["name"] = name
                    elif action == "read_archive":
                        return t
                    elif action == "interrupt":
                        if tid not in self.copilot_runtime.sessions:
                            raise ValueError("No connected Copilot turn to interrupt")
                        await self.copilot_runtime.call("session.abort", {"sessionId": tid[8:]})
                        self.copilot_runtime.finish(tid, "interrupted")
                    else:
                        if t.get("status", {}).get("type") == "active":
                            raise ValueError("Interrupt this agent before archiving")
                        destination = "archived" if action == "archive" else "threads"
                        self.state.data[collection].pop(tid)
                        self.state.data[destination][tid] = t
                        self.selected.discard(tid)
                        if tid in self.state.data["hidden"]:
                            self.state.data["hidden"].remove(tid)
                    self.state.save()
                    return {"threadId": tid, "name": t.get("name")}
        if action == "archives" and not self.state.connected:
            return {"archived": self.state.data["archived"]}
        if not self.state.connected and not copilot_action:
            raise RpcError(self.state.error or "Codex is disconnected")
        if action == "create_agent":
            name = req.get("name", "").strip()
            if not name or len(name) > 100 or not all(c.isprintable() for c in name):
                raise ValueError("Enter a name of 1–100 characters")
            model = req.get("model") or ""
            provider = req.get("provider", "codex")
            if provider not in ("codex", "copilot"):
                raise ValueError("Choose Codex or Copilot")
            if provider == "copilot":
                if not self.copilot.status.get("connected"):
                    raise RpcError("Copilot is not connected")
                validate_engine({"model": model, "effort": "", "tier": ""}, self.models_for({"provider": provider}))
            else:
                self.validate_model(model or None, None)
            workspace = self.directory / "agents" / str(uuid.uuid4())
            workspace.mkdir(parents=True, mode=0o700)
            if provider == "copilot":
                t = self.state.thread("copilot-" + str(uuid.uuid4()))
                t.update(provider="copilot", managed=True, name=name, cwd=str(workspace), model=model,
                         status={"type": "idle"}, agentConfig={"model": model}, inheritsSetupEngine=True)
                self.subscribed.add(t["id"])
                self.state.save()
                return {"threadId": t["id"], "thread": t}
            params = {"cwd": str(workspace), "sandbox": "workspace-write", "approvalPolicy": "on-request"}
            if model:
                params["model"] = model
            result = await self.rpc.call("thread/start", params)
            t = self.state.merge_thread(result["thread"])
            t.update(reportedAccess={"sandbox": result.get("sandbox"), "approvalPolicy": result.get("approvalPolicy")}, managed=True, name=name, model=result.get("model"), agentConfig={"model": model}, inheritsSetupEngine=True)
            self.subscribed.add(t["id"])
            self.state.save()
            await self.rpc.call("thread/name/set", {"threadId": t["id"], "name": name})
            return {"threadId": t["id"], "thread": t}
        if action == "rename":
            tid, name = req["threadId"], req["name"].strip()
            if not name or len(name) > 100 or any(not c.isprintable() for c in name):
                raise ValueError("Enter a name of 1–100 characters on a single line")
            async with self.locks.setdefault("catalog", asyncio.Lock()):
                collection = "archived" if tid in self.state.data["archived"] else "threads"
                if tid not in self.state.data[collection]:
                    raise ValueError("Select an agent first")
                await self.rpc.call("thread/name/set", {"threadId": tid, "name": name})
                self.state.data[collection][tid]["name"] = name
                self.state.save()
            return {"threadId": tid, "name": name}
        if action == "archives":
            async with self.locks.setdefault("catalog", asyncio.Lock()):
                return await self.archives()
        if action == "read_archive":
            result = await self.rpc.call("thread/read", {"threadId": req["threadId"], "includeTurns": True})
            return self.state.merge_thread(result["thread"], history=True, collection="archived")
        if action in ("archive", "restore"):
            tid = req["threadId"]
            async with self.locks.setdefault("catalog", asyncio.Lock()):
                async with self.locks.setdefault(tid, asyncio.Lock()):
                    if action == "archive":
                        result = await self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})
                        if result["thread"]["status"]["type"] == "active":
                            raise ValueError("The agent is working or waiting for input. Use /interrupt before archiving.")
                        saved = dict(self.state.data["threads"].get(tid, {}))
                        await self.rpc.call("thread/archive", {"threadId": tid})
                        if saved:
                            self.state.data["archived"][tid] = saved
                        self.state.merge_thread(result["thread"], collection="archived")
                        self.state.data["threads"].pop(tid, None)
                        self.selected.discard(tid)
                        self.subscribed.discard(tid)
                    else:
                        result = await self.rpc.call("thread/unarchive", {"threadId": tid})
                        old = self.state.data["archived"].pop(tid, None)
                        if old:
                            self.state.data["threads"][tid] = old
                        self.state.merge_thread(result["thread"])
                    if tid in self.state.data["hidden"]:
                        self.state.data["hidden"].remove(tid)
                    self.state.save()
                    return {"threadId": tid}
        if action == "select":
            tid = req["threadId"]
            self.selected.add(tid)
            if tid not in self.subscribed:
                await self.subscribe(tid)
            return {}
        if action == "start":
            task = next((t for t in self.state.data["tasks"] if t["id"] == req["taskId"]), None)
            if task is None:
                raise ValueError("Task not found")
            async with self.locks.setdefault(task["id"], asyncio.Lock()):
                if task["status"] not in ("planned", "setupFailed"):
                    raise ValueError("This task was already started; continue in its chat")
                task["status"] = "starting"
                self.state.save()
                try:
                    if not task.get("cwd"):
                        task.update(await create_worktree(task["repo"], self.directory / "worktrees", task["id"], task["title"]))
                        self.state.save()
                    if not task.get("threadId"):
                        settings = effective_config(self.state.data, "task:" + task["id"])
                        params = {"cwd": task["cwd"], "sandbox": "workspace-write", "approvalPolicy": "on-request"}
                        validate_engine(settings, self.state.models)
                        if settings.get("model"):
                            params["model"] = settings["model"]
                        result = await self.rpc.call("thread/start", params)
                        t = self.state.merge_thread(result["thread"])
                        t.update(managed=True, name=task["title"], model=result.get("model"), reasoningEffort=result.get("reasoningEffort"))
                        t["nextModel"], t["nextEffort"] = settings.get("model"), settings.get("effort")
                        t["projectRoot"] = task["repo"]
                        t["inheritsSetupEngine"] = True
                        t["agentConfig"] = dict(task.get("agentConfig", {}))
                        task["threadId"] = t["id"]
                        self.subscribed.add(t["id"])
                        self.state.save()
                        await self.rpc.call("thread/name/set", {"threadId": t["id"], "name": task["title"]})
                    # Persist before sending: an uncertain network result must not trigger an automatic duplicate.
                    task["status"] = "running"
                    self.state.save()
                    await self.send_message(task["threadId"], task["prompt"])
                    return task
                except Exception as error:
                    task["status"] = "needsReview" if task.get("threadId") else "setupFailed"
                    task["error"] = str(error)
                    self.state.save()
                    raise
        if action == "prepare_review":
            tid = req["threadId"]
            async with self.locks.setdefault(tid, asyncio.Lock()):
                t = self.state.thread(tid)
                if t.get("status", {}).get("type") == "active":
                    raise ValueError("Wait until the agent is ready before preparing the review branch")
                worktree = t.get("agentWorktree")
                if not worktree:
                    raise ValueError("Send a task with Isolated worktree enabled first")
                config = effective_config(self.state.data, "thread:" + tid)
                name = config["reviewBranch"] or "review/agent-" + tid[-8:]
                await git("check-ref-format", "refs/heads/" + name)
                existing = await git("-C", worktree["repo"], "for-each-ref", "--format=%(refname)", "refs/heads/" + name)
                if existing:
                    raise ValueError("That local branch already exists. Choose another Local review branch name.")
                prompt = ("Prepare my completed work for local review. In your isolated worktree only, review the diff and commit the task's intended changes locally. "
                          "Do not include secrets or unrelated files. This explicitly authorizes those local commits. "
                          "Create the local branch " + json.dumps(name) + " at the resulting commit, leaving the worktree on detached HEAD. "
                          "Do not switch, modify, stash, commit or merge in my normal checkout. Do not push or open a PR. "
                          "If uncommitted work is unrelated or the intended result is unclear, explain before proceeding. "
                          "Report the actual branch, commit, worktree path, checks performed and remaining issues so I can check out the branch myself.")
                result = await self.send_message(tid, prompt)
                t["reviewBranchRequested"] = name
                self.state.save()
                return result
        if action == "send":
            async with self.locks.setdefault(req["threadId"], asyncio.Lock()):
                return await self.send_message(req["threadId"], req["text"], req.get("clientId"), req.get("intervention", False))
        if action == "interrupt":
            tid = req["threadId"]
            t = self.state.thread(tid)
            if not t.get("turnId"):
                await self.subscribe(tid)
            if not t.get("turnId"):
                raise ValueError("No active turn to interrupt")
            return await self.rpc.call("turn/interrupt", {"threadId": tid, "turnId": t["turnId"]})
        if action == "respond":
            request = self.state.requests.get(str(req["requestId"]))
            if not request:
                raise ValueError("This request is no longer pending")
            await self.rpc.send({"id": request["id"], "result": req["response"]})
            self.state.requests.pop(str(request["id"]), None)
            return {}
        raise ValueError("Unknown action: " + str(action))

    async def send_message(self, tid, text, client_id=None, intervention=False):
        if tid in self.state.data["archived"]:
            raise ValueError("This chat is archived. Use /restore before messaging the agent.")
        if not text.strip():
            raise ValueError("Message is empty")
        if tid not in self.subscribed:
            self.observed.discard(tid)
            await self.subscribe(tid)
        t = self.state.thread(tid)
        if t.get("externalWriter"):
            raise ValueError("This agent is running in another client. Viewing is available here; send messages in its original client until that session releases control.")
        if t.get("handoffPreparing"):
            raise ValueError("Wait until the handoff workspace is ready")
        if t.get("canAcceptDirectInput") is False:
            raise ValueError("Codex does not allow direct input to this agent; use its parent chat")
        params = {"threadId": tid, "input": [{"type": "text", "text": text, "text_elements": []}]}
        config = effective_config(self.state.data, "thread:" + tid)
        profile = self.state.data.get("projectProfiles", {}).get(project_root(t), {})
        worktree = t.get("agentWorktree")
        if config["useWorktree"] and profile.get("gitRoot") and not worktree and t.get("status", {}).get("type") != "active":
            validate_engine(config, self.models_for(t), t)
            relative = Path(profile["path"]).resolve().relative_to(Path(profile["gitRoot"]).resolve())
            base_commit = await git("-C", profile["gitRoot"], "rev-parse", "--verify", "--end-of-options", (config["baseBranch"] or "HEAD") + "^{commit}")
            if relative != Path("."):
                kind = await git("-C", profile["gitRoot"], "cat-file", "-t", base_commit + ":" + relative.as_posix())
                if kind != "tree":
                    raise ValueError("The selected folder does not exist as a directory on that base branch")
            worktree = await create_worktree(profile["gitRoot"], self.directory / "worktrees", str(uuid.uuid4()),
                                            t.get("name") or "agent", base_commit, detached=True)
            worktree["root"] = worktree["cwd"]
            subdirectory = Path(worktree["cwd"]) / relative
            if subdirectory.is_dir():
                worktree["cwd"] = str(subdirectory)
            t["agentWorktree"] = worktree
            t["setupCwd"] = worktree["cwd"]
            self.state.save()
        if worktree:
            # An existing worktree remains the working location even if defaults change.
            t["setupCwd"] = worktree["cwd"]
        developer_port = config["devPort"]
        excluded = {int(v) for p in self.state.data.get("projectProfiles", {}).values()
                    for v in (p.get("config", {}).get("devPort"), p.get("config", {}).get("testPort")) if v and str(v).isdigit()}
        excluded.update(int(v) for v in (config["devPort"], config["testPort"]) if v)
        for entity in list(self.state.data["threads"].values()) + list(self.state.data["archived"].values()):
            excluded.update(entity[key] for key in ("agentPort", "agentTestPort") if entity.get(key))
        for key in ("agentPort", "agentTestPort"):
            if not t.get(key):
                t[key] = choose_port(excluded)
                excluded.add(t[key])
        if config["processes"] == "Own isolated server" and config["environment"] == "Local":
            config = dict(config, testPort=str(t["agentTestPort"]),
                          baseUrl=("https" if config["baseUrl"].startswith("https:") else "http") + "://localhost:" + str(t["agentPort"]))
        params["additionalContext"] = {
            "dashboard-progress": {"kind": "application", "value": PROGRESS_CONTEXT},
            "dashboard-setup": {"kind": "application", "value": instruction_text(config)},
        }
        params["additionalContext"]["dashboard-ports"] = {"kind": "application", "value":
            "If you are authorized to start your own server, use agent dev port " + str(t["agentPort"]) +
            " or agent test port " + str(t["agentTestPort"]) + ". The developer's configured port " + (developer_port or "(project default)") +
            " is not for agent-owned servers. Inspect package scripts and override their port using the tool's supported option or environment variable. "
            "Do not launch an unmodified script that uses the developer port. Check the port is still free immediately before starting; this is an assignment, not a socket reservation. "
            "If occupied by an unrelated process, do not stop it or silently fall back to the developer port. Report the conflict. "
            "This does not authorize starting servers when Setup or the user forbids it. Run only in your worktree and report your PID and actual URL."}
        if worktree:
            params["additionalContext"]["dashboard-worktree"] = {"kind": "application", "value":
                "Work only in the isolated Git worktree at " + json.dumps(worktree["cwd"]) + ". "
                "The normal checkout is " + json.dumps(worktree["repo"]) + "; do not modify it or switch its branch. "
                "Run installations and checks only in your worktree. It starts from committed base " + worktree["baseCommit"] + ". "
                "Do not copy uncommitted code changes from the normal checkout. Read applicable AGENTS.md and AGENTS.override.md. "
                "Imported guidance is relative to " + json.dumps(project_root(t)) + "; read its instruction files read-only if absent from the worktree. "
                "Never transfer, merge or push work automatically. Report the worktree path and actual branch or commit at completion. "
                "Keep work uncommitted unless explicitly authorized to commit, including a Prepare local branch request."}
        if t.get("handoffContext"):
            params["additionalContext"]["dashboard-handoff"] = {"kind": "application", "value": t["handoffContext"]}
        if intervention:
            params["additionalContext"]["dashboard-intervention"] = {"kind": "application", "value": INTERVENTION_CONTEXT}
        conflicts = guidance_conflicts(self.state.data.get("projectProfiles", {}).get(project_root(t), {}), config)
        if conflicts:
            params["additionalContext"]["dashboard-setup"]["value"] += "\n\n## Instruction conflicts\n" + "\n".join("- " + c for c in conflicts)
        if t.get("provider") == "copilot":
            validate_engine(config, self.models_for(t), t)
            return await self.copilot_runtime.message(t, text, config, params["additionalContext"], client_id)
        if client_id:
            params["clientUserMessageId"] = client_id
        if t.get("status", {}).get("type") == "active":
            if not t.get("turnId"):
                await self.subscribe(tid)
            if not t.get("turnId"):
                raise ValueError("Active turn is not available yet; refresh before sending")
            params["expectedTurnId"] = t["turnId"]
            result = await self.rpc.call("turn/steer", params)
        else:
            if t.get("setupCwd"):
                params["cwd"] = t["setupCwd"]
            access = config["fileAccess"]
            if access != "Keep current":
                policy = {"type": {"Read only": "readOnly", "Workspace": "workspaceWrite", "Full access": "dangerFullAccess"}[access]}
                if access != "Full access":
                    policy["networkAccess"] = config["networkAccess"]
                params["sandboxPolicy"] = policy
            if config["approvalMode"] != "Keep current":
                params["approvalPolicy"] = {"On request": "on-request", "Untrusted": "untrusted", "Never": "never"}[config["approvalMode"]]
            validate_engine(config, self.models_for(t), t)
            if config["model"]:
                params["model"] = config["model"]
                params["effort"] = config["effort"] or model_info(config, self.state.models, t).get("defaultReasoningEffort")
            elif config["effort"]:
                params["effort"] = config["effort"]
            if config["tier"]:
                params["serviceTier"] = None if config["tier"] == "default" else config["tier"]
            result = await self.rpc.call("turn/start", params)
            if params.get("cwd"):
                t["cwd"] = params["cwd"]
            reported = t.setdefault("reportedAccess", {})
            if "sandboxPolicy" in params:
                reported["sandbox"] = params["sandboxPolicy"]
            if "approvalPolicy" in params:
                reported["approvalPolicy"] = params["approvalPolicy"]
            t["lastRequestedAccess"] = {"files": config["fileAccess"], "approvals": config["approvalMode"], "network": config["networkAccess"]}
            # Notifications can arrive before the response. Never overwrite a completed turn.
            turn = result.get("turn", {})
            if turn.get("status") == "inProgress" and t.get("lastCompletedTurnId") != turn["id"]:
                t["turnId"] = turn["id"]
                if t.get("status", {}).get("type") != "active":
                    t.update(status={"type": "active", "activeFlags": []}, startedAt=turn.get("startedAt") or time.time())
        self.state.dirty = True
        return result

    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line)
                    result = await self.dispatch(request)
                    response = {"result": result}
                except Exception as error:
                    response = {"error": str(error)}
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
                await writer.drain()
        except (ConnectionError, ValueError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def run(self):
        self.stop = asyncio.Event()
        os.umask(0o077)
        lock = (self.directory / "service.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        socket_path = self.directory / "service.sock"
        if socket_path.exists():
            socket_path.unlink()
        for t in self.state.data["threads"].values():
            if t.get("handoffPreparing"):
                t.update(handoffPreparing=False, status={"type": "idle"}, activity="Handoff preparation was interrupted; inspect the workspace before continuing")
            if t.get("provider") == "copilot" and t.get("status", {}).get("type") == "active":
                t.update(status={"type": "idle"}, turnId=None, lastTurnStatus="interrupted", activity="Service restarted; send a message to resume")
        for task in self.state.data["tasks"]:
            if task["status"] == "starting":
                task["status"] = "needsReview"
                task["error"] = "Service stopped during setup; inspect the worktree before retrying"
        server = await asyncio.start_unix_server(self.client, str(socket_path), limit=32 * 1024 * 1024)
        (self.directory / "service.pid").write_text(str(os.getpid()))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        jobs = [asyncio.create_task(self.check_installation()), asyncio.create_task(self.connection_loop()), asyncio.create_task(self.maintain()),
                asyncio.create_task(self.monitor_processes()), asyncio.create_task(self.monitor_workspace()), asyncio.create_task(self.monitor_files()),
                asyncio.create_task(self.copilot.run(self.stop, self.directory,
                                    lambda: self.state.data["settings"].get("copilotHost")))]
        try:
            await self.stop.wait()
        finally:
            server.close()
            await server.wait_closed()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await self.copilot_runtime.close()
            if self.rpc:
                await self.rpc.close()
            if self.caffeine and self.caffeine.returncode is None:
                self.caffeine.terminate()
                await self.caffeine.wait()
            self.state.save()
            socket_path.unlink(missing_ok=True)
            (self.directory / "service.pid").unlink(missing_ok=True)
            lock.close()
