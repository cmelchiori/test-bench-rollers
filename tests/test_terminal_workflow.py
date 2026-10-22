from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_bench_rollers.cli import _run_attempt, _send_unlink_group
from test_bench_rollers.serial_gateway import SerialGateway
from test_bench_rollers.state_machine import BenchSession, BenchState


class TerminalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlink_group_option_sends_gateway_command(self) -> None:
        class FakeGateway:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.responses = ["<0x0399> [UNLINK] Unlinked OK"]

            async def send(self, command: str) -> None:
                self.commands.append(command)

            async def messages(self, _timeout: float, *, progress_label: str | None = None):
                del progress_label
                responses, self.responses = self.responses, []
                for response in responses:
                    yield response

        gateway = FakeGateway()
        await _send_unlink_group(gateway, 0)  # type: ignore[arg-type]
        self.assertEqual(gateway.commands, ["remote unlink group"])

    async def test_serial_send_waits_for_uart_flush(self) -> None:
        class FakeSerial:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            async def write_async(self, payload: bytes) -> None:
                self.calls.append(("write", payload))

            def flush(self) -> None:
                self.calls.append(("flush", None))

        serial = FakeSerial()
        with patch("test_bench_rollers.serial_gateway.AioSerial", return_value=serial):
            gateway = SerialGateway("fake-port")  # type: ignore[arg-type]
        await gateway.send("remote scan group")
        self.assertEqual(serial.calls, [("write", b"remote scan group\r"), ("flush", None)])

    async def test_full_attempt_sends_commands_in_order_and_reaches_pass(self) -> None:
        data = (
            "5psf-lk!5yWZ3/p<!200Yo?{2:LW48]qY?Ul&U5Q(2*90ZE*DG!ZSCcNknc0g8tGWIQ]"
            "W0=LB3074vxppivF17:x87@]Yj77j[KPxwcf4<7CU2!&n10*1DGk"
        )

        class FakeGateway:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.responses: list[str] = []

            def request_cooldown_remaining(self) -> float:
                return 0.0

            def request_cooldown_is_advisory(self) -> bool:
                return False

            async def send(self, command: str) -> None:
                self.commands.append(command)
                if command == "remote scan group":
                    self.responses = ["<0x0399> [Hi] hello"]
                elif command == "remote link 0x0399":
                    self.responses = ["<0x0399> [LINK] Linked OK"]
                elif command == "remote identify 0x0399":
                    self.responses = ["<0x0399> [MAC] aa:bb:cc:dd:ee:ff"]
                elif command == "remote request 0x0399":
                    self.responses = [f"<0x0399> [DATA]: {data}"]

            async def messages(self, _timeout: float, *, progress_label: str | None = None):
                del progress_label
                responses, self.responses = self.responses, []
                for response in responses:
                    yield response

        gateway = FakeGateway()
        session = BenchSession()
        args = SimpleNamespace(
            scan_seconds=8.0,
            link_timeout=10.0,
            identify_timeout=1.0,
            link_settle_seconds=0.0,
            response_timeout=10.0,
            rpm_min=100,
            rpm_max=3000,
            temperature_min=0,
            temperature_max=80,
        )
        with patch("test_bench_rollers.terminal_workflow._prompt", new=AsyncMock(side_effect=["0x0399", "s"])):
            await _run_attempt(gateway, args, session)  # type: ignore[arg-type]

        self.assertEqual(
            gateway.commands,
            [
                "remote scan group",
                "remote identify 0x0399",
                "remote link 0x0399",
                "remote request 0x0399",
            ],
        )
        self.assertIs(session.state, BenchState.PASSED)
        self.assertEqual(session.result, "PASS")
        self.assertEqual(session.mac_address, "AA:BB:CC:DD:EE:FF")
        self.assertIsNotNone(session.device_id)
