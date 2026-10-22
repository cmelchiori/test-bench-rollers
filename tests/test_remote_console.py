from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from textual.widgets import Input

from test_bench_rollers.remote_console import RemoteConsoleApp, RemoteConsoleServer


class FakeGateway:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.forced_requests: list[str] = []
        self.cooldown = 0.0
        self.advisory_cooldown = False
        self.subscribers: set[asyncio.Queue[str]] = set()

    async def send(self, command: str) -> None:
        self.commands.append(command)

    def request_cooldown_remaining(self) -> float:
        return self.cooldown

    def request_cooldown_is_advisory(self) -> bool:
        return self.advisory_cooldown

    async def send_forced_request(self, mesh_id: str) -> None:
        self.forced_requests.append(mesh_id)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self.subscribers.discard(queue)

    def publish_rx(self, line: str) -> None:
        for queue in self.subscribers:
            queue.put_nowait(line)


class RemoteConsoleConfigTests(unittest.TestCase):
    def test_server_accepts_a_tailscale_ipv4_bind_address(self) -> None:
        server = RemoteConsoleServer(
            port=8765,
            host="100.114.144.125",
            gateway_provider=lambda: None,
            status_provider=lambda: {},
            acquire=AsyncMock(return_value=True),
            release=AsyncMock(),
            log=lambda _message: None,
        )

        self.assertEqual(server.host, "100.114.144.125")

    def test_server_rejects_public_and_lan_bind_addresses(self) -> None:
        for host in ("0.0.0.0", "192.168.1.25", "8.8.8.8"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                RemoteConsoleServer(
                    port=8765,
                    host=host,
                    gateway_provider=lambda: None,
                    status_provider=lambda: {},
                    acquire=AsyncMock(return_value=True),
                    release=AsyncMock(),
                    log=lambda _message: None,
                )


class RemoteConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.gateway = FakeGateway()
        self.locked = False
        self.busy = False
        self.logs: list[str] = []
        self.server = RemoteConsoleServer(
            port=0,
            gateway_provider=lambda: self.gateway,  # type: ignore[arg-type]
            status_provider=lambda: {"busy": self.busy, "remote_locked": self.locked},
            acquire=self.acquire,
            release=self.release,
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

    async def event(self) -> dict[str, object]:
        raw = await asyncio.wait_for(self.reader.readline(), timeout=1)
        return json.loads(raw)

    async def send(self, value: dict[str, object]) -> None:
        self.writer.write((json.dumps(value) + "\n").encode())
        await self.writer.drain()

    async def test_console_takes_lock_sends_commands_and_releases_on_disconnect(
        self,
    ) -> None:
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

    async def test_remote_unlink_command_is_sent_after_client_confirmation(self) -> None:
        await self.event()
        await self.send({"type": "acquire"})
        await self.event()
        await self.send({"type": "command", "command": "remote unlink group"})
        sent = await self.event()
        self.assertEqual(sent["type"], "sent")
        self.assertEqual(self.gateway.commands, ["remote unlink group"])

    async def test_remote_request_bypasses_active_cooldown_like_local_retry(self) -> None:
        await self.event()
        await self.send({"type": "acquire"})
        await self.event()
        self.gateway.cooldown = 30.0

        await self.send({"type": "command", "command": "remote request 0x0503"})

        sent = await self.event()
        self.assertEqual(sent["type"], "sent")
        self.assertEqual(self.gateway.forced_requests, ["0x0503"])
        self.assertEqual(self.gateway.commands, [])

    async def test_disconnect_after_remote_unlink_releases_lock(self) -> None:
        await self.event()
        await self.send({"type": "acquire"})
        await self.event()
        await self.send({"type": "command", "command": "remote unlink group"})
        await self.event()
        self.assertEqual(self.gateway.commands, ["remote unlink group"])
        self.writer.close()
        await self.writer.wait_closed()
        for _ in range(20):
            if not self.locked:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(self.locked)


class RemoteConsoleAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_scan_sends_gateway_scan_command(self) -> None:
        app = RemoteConsoleApp("127.0.0.1", 0)
        app._send = AsyncMock()  # type: ignore[method-assign]

        async with app.run_test(size=(100, 35)) as pilot:
            app.connected = True
            app.has_control = True
            app._sync_controls()
            await pilot.click("#quick-scan")
            await pilot.pause()

            app._send.assert_awaited_once_with({"type": "command", "command": "remote scan group"})

    async def test_unlink_requires_confirmation_in_remote_console(self) -> None:
        app = RemoteConsoleApp("127.0.0.1", 0)
        app._send = AsyncMock()  # type: ignore[method-assign]

        async with app.run_test(size=(100, 35)) as pilot:
            app.connected = True
            app.has_control = True
            app._sync_controls()
            app.query_one("#command", Input).value = "remote unlink group"
            app._submit_command()
            await pilot.pause()

            self.assertTrue(app.screen.query("#cancel-unlink"))
            app._send.assert_not_awaited()

            await pilot.click("#confirm-unlink")
            await pilot.pause()

            app._send.assert_awaited_once_with({"type": "command", "command": "remote unlink group"})


if __name__ == "__main__":
    unittest.main()
