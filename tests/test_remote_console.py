from __future__ import annotations

import asyncio
import json
import unittest

from test_bench_rollers.remote_console import RemoteConsoleServer


class FakeGateway:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.subscribers: set[asyncio.Queue[str]] = set()

    async def send(self, command: str) -> None:
        self.commands.append(command)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self.subscribers.discard(queue)

    def publish_rx(self, line: str) -> None:
        for queue in self.subscribers:
            queue.put_nowait(line)


class RemoteConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.gateway = FakeGateway()
        self.locked = False
        self.busy = False
        self.logs: list[str] = []
        self.confirmed_commands: list[str] = []
        self.server = RemoteConsoleServer(
            port=0,
            gateway_provider=lambda: self.gateway,  # type: ignore[arg-type]
            status_provider=lambda: {"busy": self.busy, "remote_locked": self.locked},
            acquire=self.acquire,
            release=self.release,
            confirm_command=self.confirm,
            log=self.logs.append,
        )
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.server.bound_port)
        self.writer.write(b'{"type":"hello","client":"tester"}\n')
        await self.writer.drain()

    async def asyncTearDown(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()
        await self.server.close()

    async def acquire(self, _client: str) -> bool:
        if self.busy or self.locked:
            return False
        self.locked = True
        return True

    async def release(self, _client: str) -> None:
        self.locked = False

    async def confirm(self, _command: str) -> bool:
        self.confirmed_commands.append(_command)
        return True

    async def event(self) -> dict[str, object]:
        raw = await asyncio.wait_for(self.reader.readline(), timeout=1)
        return json.loads(raw)

    async def send(self, value: dict[str, object]) -> None:
        self.writer.write((json.dumps(value) + "\n").encode())
        await self.writer.drain()

    async def test_console_takes_lock_sends_commands_and_releases_on_disconnect(self) -> None:
        connected = await self.event()
        self.assertEqual(connected["type"], "connected")
        await self.send({"type": "acquire"})
        granted = await self.event()
        self.assertEqual(granted["type"], "control-granted")
        self.assertTrue(self.locked)

        await self.send({"type": "command", "command": "remote scan group"})
        sent = await self.event()
        self.assertEqual(sent["type"], "sent")
        self.assertEqual(self.gateway.commands, ["remote scan group"])

        self.gateway.publish_rx("<0x0503> [SCAN] Hi!")
        received = await self.event()
        self.assertEqual(received, {"type": "rx", "line": "<0x0503> [SCAN] Hi!"})

        self.writer.close()
        await self.writer.wait_closed()
        for _ in range(20):
            if not self.locked:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.locked)

    async def test_busy_bench_keeps_remote_observer_read_only(self) -> None:
        self.busy = True
        connected = await self.event()
        self.assertEqual(connected["type"], "connected")
        await self.send({"type": "acquire"})
        denied = await self.event()
        self.assertEqual(denied["type"], "control-denied")
        self.assertFalse(self.locked)

        await self.send({"type": "command", "command": "remote identify 0x0503"})
        rejected = await self.event()
        self.assertEqual(rejected["type"], "error")
        self.assertEqual(self.gateway.commands, [])

        self.gateway.publish_rx("<0x0503> [LINK] Linked OK")
        received = await self.event()
        self.assertEqual(received["type"], "rx")

    async def test_remote_unlink_command_uses_confirmation_callback(self) -> None:
        await self.event()
        await self.send({"type": "acquire"})
        await self.event()
        await self.send({"type": "command", "command": "remote unlink group"})
        sent = await self.event()
        self.assertEqual(sent["type"], "sent")
        self.assertEqual(self.gateway.commands, ["remote unlink group"])
        self.assertEqual(self.confirmed_commands, ["remote unlink group"])

    async def test_disconnect_cancels_pending_unlink_confirmation_and_releases_lock(self) -> None:
        confirmation_started = asyncio.Event()
        confirmation_cancelled = asyncio.Event()

        async def wait_for_local_confirmation(_command: str) -> bool:
            confirmation_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                confirmation_cancelled.set()
                raise

        self.server.confirm_command = wait_for_local_confirmation
        await self.event()
        await self.send({"type": "acquire"})
        await self.event()
        await self.send({"type": "command", "command": "remote unlink group"})
        await asyncio.wait_for(confirmation_started.wait(), timeout=1)
        self.writer.close()
        await self.writer.wait_closed()
        await asyncio.wait_for(confirmation_cancelled.wait(), timeout=1)
        for _ in range(20):
            if not self.locked:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.locked)


if __name__ == "__main__":
    unittest.main()
