"""Bounded HTTP/1.1 transport for an authenticated loopback child server."""
import asyncio
import base64
import json

from .rpc import RpcError


class HTTPError(RpcError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"OpenCode request failed (HTTP {status}); inspect the agent before retrying")


class LocalHTTP:
    def __init__(self, port, password):
        self.port = port
        self.authorization = base64.b64encode(('opencode:' + password).encode()).decode()

    async def open(self, method, path, body=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port, limit=4*1024*1024)
        try:
            payload = json.dumps(body).encode() if body is not None else b''
            header = (f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                      f'Authorization: Basic {self.authorization}\r\nConnection: close\r\n'
                      f'Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n')
            writer.write(header.encode() + payload)
            await writer.drain()
            raw = await reader.readuntil(b'\r\n\r\n')
            if len(raw) > 16384:
                raise RpcError('OpenCode returned an oversized HTTP header')
            lines = raw.decode('latin1').split('\r\n')
            status = int(lines[0].split()[1])
            headers = dict((k.lower(), v.strip()) for k,v in (line.split(':',1) for line in lines[1:] if ':' in line))
            if not 200 <= status < 300:
                raise HTTPError(status)
            return reader, writer, headers
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise

    async def chunks(self, reader, headers):
        if headers.get('transfer-encoding', '').lower() == 'chunked':
            while True:
                size = int((await reader.readline()).split(b';')[0].strip(),16)
                if not size:
                    return
                if size > 4*1024*1024:
                    raise RpcError('OpenCode response chunk is too large')
                yield await reader.readexactly(size)
                if await reader.readexactly(2) != b'\r\n':
                    raise RpcError('Invalid OpenCode HTTP response')
        else:
            remaining = int(headers['content-length']) if 'content-length' in headers else None
            while remaining is None or remaining > 0:
                data = await reader.read(min(65536,remaining) if remaining is not None else 65536)
                if not data:
                    if remaining:
                        raise RpcError('OpenCode response ended early')
                    return
                if remaining is not None:
                    remaining -= len(data)
                yield data

    async def request(self, method, path, body=None):
        async def exchange():
            reader, writer, headers = await self.open(method,path,body)
            try:
                data = bytearray()
                async for chunk in self.chunks(reader,headers):
                    data.extend(chunk)
                    if len(data) > 32*1024*1024:
                        raise RpcError('OpenCode response is too large')
                return json.loads(data) if data else None
            finally:
                writer.close()
                await writer.wait_closed()
        try:
            return await asyncio.wait_for(exchange(),45)
        except asyncio.TimeoutError:
            raise RpcError('OpenCode timed out; delivery is uncertain. The prompt was not retried.')

    async def events(self, ready):
        reader, writer, headers = await asyncio.wait_for(self.open('GET','/global/event'),15)
        ready.set()
        try:
            buffer = b''
            async for chunk in self.chunks(reader,headers):
                buffer = (buffer + chunk).replace(b'\r\n', b'\n')
                if len(buffer) > 4*1024*1024:
                    raise RpcError('OpenCode event is too large')
                while b'\n\n' in buffer:
                    event, buffer = buffer.split(b'\n\n',1)
                    data = b'\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith(b'data:'))
                    if data:
                        yield json.loads(data)
        finally:
            writer.close()
            await writer.wait_closed()
