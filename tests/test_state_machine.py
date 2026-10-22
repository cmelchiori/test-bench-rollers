from __future__ import annotations

import unittest

from test_bench_rollers.state_machine import BenchEvent, BenchSession, BenchState


class BenchStateMachineTests(unittest.TestCase):
    def test_success_path_requires_link_confirmation(self) -> None:
        session = BenchSession()
        session.dispatch(BenchEvent.START)
        session.dispatch(BenchEvent.READY)
        session.dispatch(BenchEvent.SCAN_STARTED)
        session.dispatch(BenchEvent.NODES_FOUND)
        session.dispatch(BenchEvent.SELECT_NODE)
        session.dispatch(BenchEvent.LINK_SENT)
        self.assertIs(session.state, BenchState.LINKING)
        session.dispatch(BenchEvent.LINK_CONFIRMED)
        session.dispatch(BenchEvent.DATA_REQUESTED)
        session.dispatch(BenchEvent.DATA_RECEIVED)
        session.dispatch(BenchEvent.PASS)
        self.assertIs(session.state, BenchState.PASSED)

    def test_rejects_events_that_do_not_match_current_state(self) -> None:
        with self.assertRaises(ValueError):
            BenchSession().dispatch(BenchEvent.DATA_RECEIVED)

    def test_reset_clears_attempt_data(self) -> None:
        session = BenchSession(state=BenchState.PASSED, nodes={"0x0001"}, selected_node="0x0001")
        session.telemetry = {"schema": "roller-data-1.0"}
        session.mac_address = "AA:BB:CC:DD:EE:FF"
        session.device_id = "roller-01"
        session.result = "PASS"
        session.dispatch(BenchEvent.RESET)
        self.assertIs(session.state, BenchState.SCANNING)
        self.assertFalse(session.nodes)
        self.assertIsNone(session.selected_node)
        self.assertIsNone(session.telemetry)
        self.assertIsNone(session.mac_address)
        self.assertIsNone(session.device_id)
        self.assertIsNone(session.result)

    def test_identity_can_be_pending_and_then_resumed(self) -> None:
        session = BenchSession()
        session.dispatch(BenchEvent.START)
        session.dispatch(BenchEvent.READY)
        session.dispatch(BenchEvent.SCAN_STARTED)
        session.dispatch(BenchEvent.NODES_FOUND)
        session.dispatch(BenchEvent.SELECT_NODE)
        session.dispatch(BenchEvent.LINK_CONFIRMED)
        session.dispatch(BenchEvent.DATA_REQUESTED)
        session.dispatch(BenchEvent.DATA_RECEIVED)
        session.dispatch(BenchEvent.IDENTITY_INCOMPLETE)
        self.assertIs(session.state, BenchState.IDENTITY_PENDING)
        session.dispatch(BenchEvent.IDENTITY_COMPLETE)
        self.assertIs(session.state, BenchState.EVALUATING)

    def test_empty_scan_can_be_retried(self) -> None:
        session = BenchSession()
        session.dispatch(BenchEvent.START)
        session.dispatch(BenchEvent.READY)
        session.dispatch(BenchEvent.SCAN_STARTED)
        session.dispatch(BenchEvent.SCAN_EMPTY)
        self.assertIs(session.state, BenchState.NO_NODES)
        session.dispatch(BenchEvent.RESET)
        self.assertIs(session.state, BenchState.SCANNING)
