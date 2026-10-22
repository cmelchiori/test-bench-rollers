from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from textual.widgets import Button, RichLog, Select, Static, TabbedContent

from test_bench_rollers.state_machine import BenchState
from test_bench_rollers.tui import BenchTuiApp


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_scan_has_explicit_status_and_can_be_retried(self) -> None:
        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.scan_count = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                if command == "remote scan group":
                    self.scan_count += 1

            async def messages(self, _timeout: float):
                if self.scan_count > 1:
                    yield "<0x0399> [SCAN] Hi"

        args = SimpleNamespace(port="fake", baudrate=115200, scan_seconds=0.01)
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#scan")
            for _ in range(20):
                await pilot.pause(0.02)
                if app.session.state is BenchState.NO_NODES:
                    break
            self.assertIs(app.session.state, BenchState.NO_NODES)
            self.assertIn("sin_nodos", app._status_text())
            summary = app.query_one("#home-summary", Static).render()
            self.assertIn("No se detectaron nodos", summary.plain)

            await pilot.click("#scan")
            for _ in range(20):
                await pilot.pause(0.02)
                if app.session.state is BenchState.SELECTING:
                    break
            self.assertIs(app.session.state, BenchState.SELECTING)

    async def test_remote_client_locks_local_actions_sends_uart_and_unlocks_on_disconnect(
        self,
    ) -> None:
        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []
                self.subscribers: set[asyncio.Queue[str]] = set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)

            async def messages(self, _timeout: float):
                if False:
                    yield ""

            def subscribe(self):
                queue: asyncio.Queue[str] = asyncio.Queue()
                self.subscribers.add(queue)
                return queue

            def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
                self.subscribers.discard(queue)

        args = SimpleNamespace(port="fake-port", baudrate=115200, remote_port=0)
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.sleep(0.05)
            reader, writer = await asyncio.open_connection("127.0.0.1", app.remote_console.bound_port)  # type: ignore[union-attr]
            writer.write(b'{"type":"hello","client":"qa"}\n{"type":"acquire"}\n')
            await writer.drain()

            async def wait_for_event(event_type: str) -> dict[str, object]:
                while True:
                    value = json.loads(await asyncio.wait_for(reader.readline(), timeout=1))
                    if value["type"] == event_type:
                        return value

            await wait_for_event("control-granted")
            self.assertTrue(app._remote_active)
            self.assertTrue(app.query_one("#scan", Button).disabled)
            writer.write(b'{"type":"command","command":"remote scan group"}\n')
            await writer.drain()
            await wait_for_event("sent")
            self.assertEqual(app.gateway.commands, ["remote scan group"])  # type: ignore[union-attr]

            writer.write(b'{"type":"command","command":"remote unlink group"}\n')
            await writer.drain()
            for _ in range(30):
                await pilot.pause(0.01)
                if app.screen.query("#cancel-unlink"):
                    break
            self.assertTrue(app.screen.query("#cancel-unlink"))
            await pilot.click("#cancel-unlink")
            await wait_for_event("error")
            self.assertEqual(app.gateway.commands, ["remote scan group"])  # type: ignore[union-attr]

            writer.close()
            await writer.wait_closed()
            for _ in range(30):
                if not app._remote_active:
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(app._remote_active)
            self.assertFalse(app.query_one("#scan", Button).disabled)

    async def test_unlink_cancel_does_not_send_command(self) -> None:
        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)

        app = BenchTuiApp(SimpleNamespace(port="fake", baudrate=115200), gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#unlink")
            await pilot.click("#cancel-unlink")
            await pilot.pause()
            self.assertEqual(app.gateway.commands, [])  # type: ignore[union-attr]

    async def test_tui_unlink_group_logs_received_processing(self) -> None:
        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)

            async def messages(self, _timeout: float):
                yield "<0x0399> [UNLINK] Unlinked OK"

        args = SimpleNamespace(port="fake-port", baudrate=115200, unlink_listen_seconds=0.01)
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#unlink")
            await pilot.click("#confirm-unlink")
            for _ in range(20):
                await pilot.pause(0.05)
                if not app._busy:
                    break
            self.assertEqual(app.gateway.commands, ["remote unlink group"])  # type: ignore[union-attr]
            self.assertEqual(app.session.last_serial_message, "<0x0399> [UNLINK] Unlinked OK")

    async def test_tui_mounts_with_serial_controls_and_node_selector(self) -> None:
        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

        args = SimpleNamespace(port="fake-port", baudrate=115200)
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.query_one("#scan", Button), Button)
            self.assertIsInstance(app.query_one("#test", Button), Button)
            self.assertIsInstance(app.query_one("#unlink", Button), Button)
            self.assertIsInstance(app.query_one("#node-select", Select), Select)
            self.assertIsInstance(app.query_one("#serial-log", RichLog), RichLog)
            tabs = app.query_one("#main-tabs", TabbedContent)
            self.assertEqual(tabs.active, "operation-tab")
            self.assertIn(("q", "quit_app", "Salir"), app.BINDINGS)
            self.assertIsNotNone(app.gateway)
            await pilot.click(tabs.get_tab("debug-tab"))
            self.assertEqual(tabs.active, "debug-tab")
            self.assertIsInstance(app.query_one("#serial-log", RichLog), RichLog)
            await pilot.click(tabs.get_tab("operation-tab"))
            self.assertEqual(tabs.active, "operation-tab")
            self.assertTrue(await app._acquire_remote("debugger"))
            self.assertTrue(app.query_one("#scan", Button).disabled)
            self.assertIn("BLOQUEADO POR ACCESO REMOTO", app._status_text())
            await app._release_remote("debugger")
            self.assertFalse(app.query_one("#scan", Button).disabled)

    async def test_tui_reports_serial_open_failure_in_session_state(self) -> None:
        class BrokenGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                raise OSError("puerto ocupado")

        args = SimpleNamespace(port="busy-port", baudrate=115200)
        app = BenchTuiApp(args, gateway_factory=BrokenGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(10):
                await pilot.pause(0.05)
                if app.session.state is BenchState.ERROR:
                    break
            self.assertIs(app.session.state, BenchState.ERROR)
            self.assertIn("puerto ocupado", app.session.error or "")

    async def test_tui_buttons_run_scan_link_and_telemetry_flow(self) -> None:
        data = (
            "5psf-lk!5yWZ3/p<!200Yo?{2:LW48]qY?Ul&U5Q(2*90ZE*DG!ZSCcNknc0g8tGWIQ]"
            "W0=LB3074vxppivF17:x87@]Yj77j[KPxwcf4<7CU2!&n10*1DGk"
        )

        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []
                self.responses: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)
                self.responses = {
                    "remote scan group": ["<0x0399> [SCAN] Hi!"],
                    "remote identify 0x0399": ["<0x0399> [MAC] aa:bb:cc:dd:ee:ff"],
                    "remote link 0x0399": ["<0x0399> [LINK] Linked OK"],
                    "remote request 0x0399": [f"<0x0399> [DATA]: {data}"],
                }[command]

            async def messages(self, _timeout: float):
                responses, self.responses = self.responses, []
                for response in responses:
                    yield response

        args = SimpleNamespace(
            port="fake-port",
            baudrate=115200,
            scan_seconds=1,
            link_timeout=1,
            identify_timeout=1,
            link_settle_seconds=0,
            response_timeout=1,
            rpm_min=100,
            rpm_max=3000,
            temperature_min=0,
            temperature_max=80,
        )
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#scan")
            for _ in range(20):
                await pilot.pause(0.05)
                if app.session.state is BenchState.SELECTING:
                    break
            self.assertIs(app.session.state, BenchState.SELECTING)
            await pilot.click("#test")
            for _ in range(20):
                await pilot.pause(0.05)
                if app.session.state is BenchState.PASSED:
                    break
            self.assertIs(app.session.state, BenchState.PASSED)
            self.assertEqual(app.session.result, "PASS")
            self.assertEqual(app.query_one("#main-tabs", TabbedContent).active, "operation-tab")

    async def test_multiple_nodes_wait_for_manual_selection_and_test(self) -> None:
        data = "5psf-lk!5yWZ3/p<!200Yo?{2:LW48]qY?Ul&U5Q(2*90ZE*DG!ZSCcNknc0g8tGWIQ]W0=LB3074vxppivF17:x87@]Yj77j[KPxwcf4<7CU2!&n10*1DGk"

        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []
                self.responses: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)
                self.responses = {
                    "remote scan group": ["<0x0399> [SCAN] Hi", "<0x0398> [SCAN] Hi"],
                    "remote identify 0x0398": ["<0x0398> [MAC] aa:bb:cc:dd:ee:ff"],
                    "remote link 0x0398": ["<0x0398> [LINK] Linked OK"],
                    "remote request 0x0398": [f"<0x0398> [DATA]: {data}"],
                }.get(command, [])

            async def messages(self, _timeout: float):
                responses, self.responses = self.responses, []
                for response in responses:
                    yield response

        args = SimpleNamespace(
            port="fake",
            baudrate=115200,
            scan_seconds=0.01,
            identify_timeout=0.01,
            link_timeout=0.01,
            link_settle_seconds=0,
            response_timeout=0.01,
            rpm_min=100,
            rpm_max=3000,
            temperature_min=0,
            temperature_max=80,
        )
        app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#scan")
            for _ in range(20):
                await pilot.pause(0.02)
                if app.session.state is BenchState.SELECTING:
                    break
            self.assertIs(app.session.state, BenchState.SELECTING)
            self.assertEqual(app.gateway.commands, ["remote scan group"])  # type: ignore[union-attr]
            selector = app.query_one("#node-select", Select)
            selector.value = "0x0398"
            await pilot.click("#test")
            for _ in range(20):
                await pilot.pause(0.02)
                if app.session.state is BenchState.PASSED:
                    break
            self.assertIs(app.session.state, BenchState.PASSED)
            self.assertEqual(app.session.selected_node, "0x0398")

    async def test_identity_retry_only_sends_missing_mac_query(self) -> None:
        payload = {
            "schema": "roller-data-1.0",
            "tags": {"device_id": "roller-01"},
            "fields": {
                "rpm": 500,
                "temperature": [25],
                "alarms": {"latched": {"all": 0}},
            },
        }

        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []
                self.mac_replies = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)

            async def messages(self, _timeout: float):
                command = self.commands[-1]
                if command == "remote scan group":
                    yield "<0x0399> [SCAN] Hi"
                elif command == "remote identify 0x0399":
                    self.mac_replies += 1
                    if self.mac_replies > 1:
                        yield "<0x0399> [MAC] aa:bb:cc:dd:ee:ff"
                elif command == "remote link 0x0399":
                    yield "<0x0399> [LINK] Linked OK"
                elif command == "remote request 0x0399":
                    yield "<0x0399> [DATA]: fake"

        args = SimpleNamespace(
            port="fake",
            baudrate=115200,
            scan_seconds=0.01,
            identify_timeout=0.01,
            link_timeout=0.01,
            link_settle_seconds=0,
            response_timeout=0.01,
            rpm_min=100,
            rpm_max=3000,
            temperature_min=0,
            temperature_max=80,
        )
        with (
            patch("test_bench_rollers.bench_workflow.decode_telemetry", return_value=("0x0399", payload)),
            patch("test_bench_rollers.tui.decode_telemetry", return_value=("0x0399", payload)),
        ):
            app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.click("#scan")
                for _ in range(30):
                    await pilot.pause(0.02)
                    if app.session.state is BenchState.SELECTING:
                        break
                self.assertIs(app.session.state, BenchState.SELECTING)
                await pilot.click("#test")
                for _ in range(30):
                    await pilot.pause(0.02)
                    if app.session.state is BenchState.IDENTITY_PENDING:
                        break
                self.assertIs(app.session.state, BenchState.IDENTITY_PENDING, app.session.error)
                await app._retry_identity()
                self.assertIs(app.session.state, BenchState.PASSED)
                self.assertEqual(
                    app.gateway.commands,
                    [  # type: ignore[union-attr]
                        "remote scan group",
                        "remote identify 0x0399",
                        "remote link 0x0399",
                        "remote request 0x0399",
                        "remote identify 0x0399",
                    ],
                )

    async def test_identity_retry_only_requests_telemetry_when_device_id_is_missing(
        self,
    ) -> None:
        payload = {
            "schema": "roller-data-1.0",
            "tags": {"device_id": "roller-01"},
            "fields": {
                "rpm": 500,
                "temperature": [25],
                "alarms": {"latched": {"all": 0}},
            },
        }

        class FakeGateway:
            def __init__(self, _port: str, _baudrate: int) -> None:
                self.commands: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, command: str) -> None:
                self.commands.append(command)

            async def messages(self, _timeout: float):
                yield "<0x0399> [DATA]: fake"

        args = SimpleNamespace(
            port="fake",
            baudrate=115200,
            response_timeout=0.01,
            rpm_min=100,
            rpm_max=3000,
            temperature_min=0,
            temperature_max=80,
        )
        with patch("test_bench_rollers.tui.decode_telemetry", return_value=("0x0399", payload)):
            app = BenchTuiApp(args, gateway_factory=FakeGateway)  # type: ignore[arg-type]
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.session.state = BenchState.IDENTITY_PENDING
                app.session.nodes.add("0x0399")
                app.session.selected_node = "0x0399"
                app.session.mac_address = "AA:BB:CC:DD:EE:FF"
                app.session.telemetry = payload
                app._set_busy(False)
                await app._retry_identity()
                self.assertEqual(app.gateway.commands, ["remote request 0x0399"])  # type: ignore[union-attr]
                self.assertIs(app.session.state, BenchState.PASSED)


if __name__ == "__main__":
    unittest.main()
