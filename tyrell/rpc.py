"""Multiplexed JSON-RPC for an owned stdio server or a legacy daemon proxy."""
import asyncio
import contextlib
import json

from .websocket import WebSocket


class RpcError(RuntimeError):
    pass


class Rpc:
    def __init__(self, command, on_event, on_request, env=None):
        self.command = command
        self.env = env
        self.on_event = on_event
        self.on_request = on_request
        self.pending = {}
        self.sequence = 0
        self.process = None
        self.reader_task = None
        self.stderr_task = None
        self.stderr = ""
        self.websocket = None

    async def connect(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024, env=self.env)
        self.stderr_task = asyncio.create_task(self.read_stderr())
        if "proxy" in self.command:
            self.websocket = WebSocket(self.process.stdout, self.process.stdin)
            try:
                await self.websocket.handshake()
            except asyncio.IncompleteReadError as error:
                # EOF during the HTTP upgrade must enter the service retry loop.
                if self.stderr_task:
                    try:
                        await asyncio.wait_for(asyncio.shield(self.stderr_task), .5)
                    except asyncio.TimeoutError:
                        pass
                raise RpcError("Codex proxy disconnected during handshake. " + self.stderr[-500:]) from error
        self.reader_task = asyncio.create_task(self.read())
        await self.call("initialize", {"clientInfo": {"name": "tyrell", "title": "Tyrell Agent Management", "version": "0.1.0"},
                                       "capabilities": {"experimentalApi": True}})
        await self.send({"method": "initialized"})

    async def send(self, value):
        if not self.process or self.process.returncode is not None:
            raise RpcError("Codex connection is closed")
        if self.websocket:
            await self.websocket.send(json.dumps(value))
        else:
            self.process.stdin.write((json.dumps(value) + "\n").encode())
            await self.process.stdin.drain()

    async def call(self, method, params, timeout=45):
        self.sequence += 1
        rid = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.send({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise RpcError("Codex timed out for " + method + ". Check status before retrying; it may have been accepted.")
        finally:
            self.pending.pop(rid, None)

    async def read_stderr(self):
        while True:
            chunk = await self.process.stderr.read(4096)
            if not chunk:
                return
            self.stderr = (self.stderr + chunk.decode(errors="replace"))[-4000:]

    async def read(self):
        try:
            while True:
                line = await self.websocket.read() if self.websocket else await self.process.stdout.readline()
                if not line:
                    break
                value = json.loads(line)
                if "method" in value:
                    if "id" in value:
                        self.on_request(value)
                    else:
                        self.on_event(value["method"], value.get("params", {}))
                elif value.get("id") in self.pending:
                    f = self.pending[value["id"]]
                    if not f.done():
                        if "error" in value:
                            f.set_exception(RpcError(value["error"].get("message", str(value["error"]))))
                        else:
                            f.set_result(value.get("result", {}))
        except asyncio.IncompleteReadError:
            pass
        finally:
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(RpcError("Codex server disconnected. " + self.stderr[-500:]))

    async def close(self):
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()  # Only our own child, never a shared daemon.
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
        for task in (self.reader_task, self.stderr_task):
            if task and not task.done():
                task.cancel()
