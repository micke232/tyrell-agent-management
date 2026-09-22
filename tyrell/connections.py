"""Read-only provider connectivity. Never creates or resumes a Copilot session."""
import asyncio
import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse


LABELS = {
    "connected": ("Connected", "success"),
    "connecting": ("Connecting", "working"),
    "signin": ("Sign in", "warning"),
    "missing": ("Not installed", "muted"),
    "unavailable": ("Unavailable", "warning"),
    "offline": ("Offline", "warning"),
    "account": ("Switch account", "warning"),
}


def normalize_host(value):
    parsed = urlparse(value if "://" in value else "https://" + value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port
            or any(not c.isascii() or not (c.isalnum() or c in ".-") for c in parsed.hostname)):
        raise ValueError("Use an HTTPS GitHub host, for example https://company.ghe.com")
    return "https://" + parsed.hostname.lower()


def copilot_executable():
    configured = os.environ.get("AGENT_HUB_COPILOT")
    candidates = [configured] if configured else [shutil.which("copilot"), str(Path.home() / ".local/bin/copilot")]
    for candidate in candidates:
        if candidate:
            found = shutil.which(os.path.expanduser(candidate))
            if found:
                return found
    return None


class ConnectionError(RuntimeError):
    """Intentionally excludes server text: auth errors may contain credentials."""


class CopilotProbe:
    METHODS = {"ping", "auth.getStatus", "models.list"}

    def __init__(self):
        self.process = None
        self.sequence = 0

    async def start(self, executable, cwd, host=None):
        env = dict(os.environ)
        if host:
            # Scope account routing to this child; never switch the CLI's global account.
            env["COPILOT_GH_HOST"] = urlparse(normalize_host(host)).hostname
        self.process = await asyncio.create_subprocess_exec(
            executable, "--headless", "--stdio", "--no-auto-update", "--log-level", "none",
            cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)

    async def send(self, message):
        payload = json.dumps(message).encode("utf-8")
        self.process.stdin.write(("Content-Length: %d\r\n\r\n" % len(payload)).encode("ascii") + payload)
        await self.process.stdin.drain()

    async def call(self, method, timeout=15):
        if method not in self.METHODS:
            raise ValueError("Connection checks cannot run agent work")
        self.sequence += 1
        rid = self.sequence

        async def exchange():
            await self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": {}})
            while True:
                header = await self.process.stdout.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise ConnectionError("Invalid protocol header")
                fields = dict(line.split(b":", 1) for line in header.strip().split(b"\r\n"))
                length = int(next((value for key, value in fields.items() if key.lower() == b"content-length"), b"0"))
                if not 0 < length <= 4 * 1024 * 1024:
                    raise ConnectionError("Invalid protocol message size")
                message = json.loads(await self.process.stdout.readexactly(length))
                if not isinstance(message, dict):
                    raise ConnectionError("Invalid protocol response")
                if "method" in message:
                    if "id" in message:
                        await self.send({"jsonrpc": "2.0", "id": message["id"],
                                         "error": {"code": -32601, "message": "Read-only connection monitor"}})
                    continue
                if message.get("id") != rid:
                    continue
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise ConnectionError("Connection request failed")
                return message["result"]

        return await asyncio.wait_for(exchange(), timeout)

    async def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(process.communicate(), 3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()


class CopilotConnection:
    def __init__(self):
        self.status = {"name": "Copilot", "status": "connecting", "connected": False, "models": []}

    def update(self, state, **details):
        self.status = {"name": "Copilot", "status": state, "connected": state == "connected",
                       "models": [], "checkedAt": time.time(), **details}

    async def run(self, stop, directory, expected_host=lambda: None):
        while not stop.is_set():
            probe = CopilotProbe()
            try:
                executable = copilot_executable()
                if not executable:
                    self.update("missing")
                else:
                    await probe.start(executable, str(directory), host=expected_host())
                    ping = await probe.call("ping")
                    identity = None
                    models = []
                    while not stop.is_set():
                        auth = await probe.call("auth.getStatus")
                        if auth.get("isAuthenticated") is not True:
                            self.update("signin", protocolVersion=ping.get("protocolVersion"))
                            break  # Restart to pick up credentials saved by an external login.
                        current = (auth.get("login"), auth.get("host"))
                        host = normalize_host(auth["host"]) if isinstance(auth.get("host"), str) else None
                        if expected_host() and host != expected_host():
                            self.update("account", host=host)
                            break
                        if identity != current or not models:
                            try:
                                result = await probe.call("models.list")
                                models = [{"id": m["id"], "name": m.get("name") or m["id"]}
                                          for m in result.get("models", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
                            except ConnectionError:
                                self.update("unavailable", authenticated=True)
                                break
                            identity = current
                        if not models:
                            self.update("unavailable", authenticated=True)
                            break
                        self.update("connected", authenticated=True, models=models,
                                    host=host, protocolVersion=ping.get("protocolVersion"))
                        try:
                            await asyncio.wait_for(stop.wait(), 15)
                        except asyncio.TimeoutError:
                            ping = await probe.call("ping")
            except (OSError, ValueError, KeyError, TypeError, ConnectionError, asyncio.TimeoutError,
                    asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                self.update("offline")
            finally:
                await probe.close()
            try:
                await asyncio.wait_for(stop.wait(), 10)
            except asyncio.TimeoutError:
                pass


def providers(data):
    return data.get("providers") or {
        "codex": {"name": "Codex", "status": "connected" if data.get("connected") else "connecting",
                  "models": [{"id": m["model"], "name": m.get("displayName") or m["model"]} for m in data.get("models", [])]},
        "copilot": {"name": "Copilot", "status": "missing", "models": []},
    }


def connection_badges(data, available, demo=False):
    values = providers(data)
    result = []
    for key, name in (("codex", "Codex"), ("copilot", "Copilot"), ("opencode", "OpenCode")):
        state = values.get(key, {}).get("status", "offline")
        label, tone = LABELS.get(state, LABELS["offline"])
        if demo:
            label, tone = "Demo", "muted"
        result.append((name, label, tone, "●" if state == "connected" and not demo else "○"))
    if sum(len(name) + len(label) + 5 for name, label, _, _ in result) > available:
        short = {"Connected": "Online", "Connecting": "Wait", "Not installed": "Missing", "Unavailable": "Limited", "Switch account": "Account"}
        result = [(name, short.get(label, label), tone, dot) for name, label, tone, dot in result]
    if sum(len(name) + len(label) + 5 for name, label, _, _ in result) > available:
        return [(dot + " " + name, tone) for name, _, tone, dot in result]
    return [(dot + " " + name + " " + label, tone) for name, label, tone, dot in result]


def connections_text(data, demo=False):
    lines = ["CONNECTIONS" + (" · DEMO" if demo else ""), ""]
    for key, name in (("codex", "Codex"), ("copilot", "GitHub Copilot"), ("opencode", "OpenCode")):
        info = providers(data).get(key, {})
        state = info.get("status", "offline")
        label = "Demo" if demo else LABELS.get(state, LABELS["offline"])[0]
        lines.extend([name + " · " + label])
        models = info.get("models", [])
        if state == "connected" and not demo:
            lines.append("Reported models (%d):" % len(models))
            lines.extend("  " + m.get("name", m.get("id", "Unknown")) for m in models)
        if key == "opencode":
            lines.append("Install OpenCode CLI and configure your own model access: tyrell login opencode")
            lines.append("Local models are supported. No subscription or model access is included with Tyrell.")
        if key == "copilot":
            host = info.get("expectedHost") or info.get("host")
            if host:
                lines.append("GitHub host: " + host)
            if state in ("signin", "account"):
                lines.append("Sign in from your terminal: tyrell login copilot" + (" --host " + host if host else ""))
            elif state == "missing":
                lines.append("Install Copilot CLI to connect it.")
            elif state == "unavailable":
                lines.append("Signed in, but the model catalog is unavailable. Check Copilot access and organization policy.")
            lines.append("F3 creates an agent. F6 hands work to a new agent in a separate worktree.")
            verification = info.get("repositoryVerification") or {}
            if verification.get("readable") and verification.get("host") == host:
                checked = time.strftime("%Y-%m-%d %H:%M", time.localtime(verification["checkedAt"]))
                lines.append("Repository read verified: " + verification["repository"] + " via " + verification["method"] + " · " + checked)
                lines.append("Git access verified; separate GitHub MCP connector access is not implied.")
            else:
                lines.append("Verify repository access from the Copilot agent's Setup tab.")
        lines.append("")
    lines.append("Connection checks do not send prompts or run tools.")
    return "\n".join(lines)
