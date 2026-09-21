import asyncio
import base64
import hashlib
import struct
import unittest

from codex_dashboard.websocket import WebSocket


class Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, value):
        self.data.extend(value)

    async def drain(self):
        pass


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmentation_with_interleaved_ping(self):
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(b'\x01\x03hel\x89\x01?\x80\x02lo')
        ws = WebSocket(reader, writer)
        self.assertEqual(await ws.read(), "hello")
        self.assertEqual(writer.data[0], 0x8a)
        self.assertEqual(writer.data[1], 0x81)
        self.assertEqual(writer.data[6] ^ writer.data[2], ord("?"))

    async def test_client_frames_are_masked_for_all_payload_sizes(self):
        for size in (5, 130, 70000):
            writer = Writer()
            ws = WebSocket(None, writer)
            payload = b"x" * size
            await ws.send(payload)
            frame = writer.data
            self.assertEqual(frame[0], 0x81)
            self.assertTrue(frame[1] & 0x80)
            offset = 2 if size < 126 else 4 if size < 65536 else 10
            mask = frame[offset:offset + 4]
            decoded = bytes(c ^ mask[i % 4] for i, c in enumerate(frame[offset + 4:]))
            self.assertEqual(decoded, payload)

    async def test_handshake_is_validated(self):
        reader, writer = asyncio.StreamReader(), Writer()
        ws = WebSocket(reader, writer)
        task = asyncio.create_task(ws.handshake())
        await asyncio.sleep(0)
        key = bytes(writer.data).split(b"Sec-WebSocket-Key: ")[1].split(b"\r\n")[0]
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        reader.feed_data(b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        await task

    async def test_oversized_server_frame_is_rejected_before_allocation(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'\x81\x7f' + struct.pack("!Q", 1 << 40))
        with self.assertRaisesRegex(ValueError, "limit"):
            await WebSocket(reader, Writer()).read()
