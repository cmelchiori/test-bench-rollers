from __future__ import annotations

import unittest

from test_bench_rollers.bench_workflow import BenchWorkflow
from test_bench_rollers.state_machine import BenchSession, BenchState


class FakeGateway:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.scan_responses: list[str] = []
        self.commands: list[str] = []

    async def send(self, command: str) -> None:
        self.commands.append(command)
        if command == "remote scan group":
            self.responses = self.scan_responses.copy()

    async def messages(self, _timeout: float, *, progress_label: str | None = None):
        del progress_label
        responses, self.responses = self.responses, []
        for response in responses:
            yield response


class BenchWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_scan_transitions_to_no_nodes_and_next_scan_recovers(self) -> None:
        gateway = FakeGateway()
        session = BenchSession()
        workflow = BenchWorkflow(gateway, session)

        await workflow.scan(0)
        self.assertIs(session.state, BenchState.NO_NODES)
        self.assertEqual(session.nodes, set())

        gateway.scan_responses = ["<0x0399> [SCAN] Hi"]
        await workflow.scan(0)
        self.assertIs(session.state, BenchState.SELECTING)
        self.assertEqual(session.nodes, {"0x0399"})
        self.assertEqual(gateway.commands, ["remote scan group", "remote scan group"])
