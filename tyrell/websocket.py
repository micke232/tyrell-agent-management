"""Minimal RFC 6455 transport over the app-server proxy's stdio byte stream.

The proxy is a raw Unix-socket bridge, not the app server's JSONL transport.
No compression/extensions are negotiated. Client frames must be masked.
"""
import asyncio
import base64
import hashlib
import os
import struct


MAX_MESSAGE = 32 * 1024 * 1024


class WebSocket:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        self.writer.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: "
                           + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await self.writer.drain()
        raw = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), 10)
        if len(raw) > 16384:
            raise ValueError("Oversized WebSocket handshake")
        lines = raw.decode("latin1").split("\r\n")
        if lines[0].split(" ")[1] != "101":
            raise ValueError("Codex WebSocket handshake failed: " + lines[0])
        headers = {k.strip().lower(): v.strip() for line in lines[1:] if ":" in line for k, v in [line.split(":", 1)]}
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise ValueError("Invalid WebSocket handshake from Codex")

    async def send(self, payload, opcode=1):
        if isinstance(payload, str):
            payload = payload.encode()
        n = len(payload)
        if n > MAX_MESSAGE:
            raise ValueError("Message exceeds transport limit")
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.writer.write(header + mask + masked)
        await self.writer.drain()

    async def read(self):
        parts = bytearray()
        started = False
        while True:
            head = await self.reader.readexactly(2)
            final, opcode, masked = bool(head[0] & 128), head[0] & 15, bool(head[1] & 128)
            if head[0] & 0x70 or masked:
                raise ValueError("Unsupported WebSocket frame")
            n = head[1] & 127
            if n == 126:
                n = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if n + len(parts) > MAX_MESSAGE:
                raise ValueError("Codex message exceeds transport limit")
            if opcode >= 8 and (not final or n > 125):
                raise ValueError("Invalid WebSocket control frame")
            payload = await self.reader.readexactly(n)
            if opcode == 8:
                return None
            if opcode == 9:
                await self.send(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise ValueError("Expected a WebSocket text message")
            parts.extend(payload)
            if final:
                return parts.decode("utf-8")
