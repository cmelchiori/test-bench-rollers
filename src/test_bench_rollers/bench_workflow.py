"""Shared serial workflow for scanning and testing a selected roller node."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .evaluation import evaluate
from .serial_gateway import SerialGateway, decode_telemetry, link_result, mac_result, normalize_mesh_id
from .state_machine import BenchEvent, BenchSession, BenchState

StatusCallback = Callable[[str], None]
MessageCallback = Callable[[str], None]
NodeCallback = Callable[[str], None]
RequestApproval = Callable[[float, bool], Awaitable[bool]]
RequestSender = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class WorkflowResult:
    state: BenchState
    failures: list[str] = field(default_factory=list)
    elapsed: float | None = None


class BenchWorkflow:
    """Own the serial protocol sequence while callers provide interaction hooks."""

    def __init__(
        self,
        gateway: SerialGateway,
        session: BenchSession,
        *,
        on_status: StatusCallback | None = None,
        on_message: MessageCallback | None = None,
        on_node: NodeCallback | None = None,
        progress_labels: bool = True,
    ) -> None:
        self.gateway = gateway
        self.session = session
        self.on_status = on_status or (lambda _message: None)
        self.on_message = on_message or (lambda _message: None)
        self.on_node = on_node or (lambda _mesh_id: None)
        self.progress_labels = progress_labels

    def _messages(self, seconds: float, label: str):
        if self.progress_labels:
            return self.gateway.messages(seconds, progress_label=label)
        return self.gateway.messages(seconds)

    def _prepare_scan(self) -> None:
        state = self.session.state
        if state is BenchState.IDLE:
            self.session.dispatch(BenchEvent.START)
            self.session.dispatch(BenchEvent.READY)
        elif state in {
            BenchState.NO_NODES,
            BenchState.PASSED,
            BenchState.FAILED,
            BenchState.ERROR,
            BenchState.IDENTITY_PENDING,
        }:
            self.session.dispatch(BenchEvent.RESET)
        elif state is BenchState.SELECTING:
            self.session.dispatch(BenchEvent.CANCEL)
        elif state is not BenchState.SCANNING:
            raise RuntimeError(f"No se puede buscar durante {state.value}")

        self.session.nodes.clear()
        self.session.selected_node = None
        self.session.mac_address = None
        self.session.device_id = None
        self.session.telemetry = None
        self.session.result = None
        self.session.error = None
        self.session.dispatch(BenchEvent.SCAN_STARTED)

    async def scan(self, seconds: float) -> set[str]:
        """Scan the mesh and leave the session in SELECTING or NO_NODES."""
        self._prepare_scan()
        await self.gateway.send("remote scan group")
        self.on_status(f"TX remote scan group; escuchando por {seconds:g} s…")
        async for raw in self._messages(seconds, "Búsqueda"):
            self.session.last_serial_message = raw
            self.on_message(raw)
            mesh_match = decode_mesh_id(raw)
            if mesh_match is None or mesh_match in self.session.nodes:
                continue
            self.session.nodes.add(mesh_match)
            self.on_node(mesh_match)
            self.on_status(f"Nodo detectado: {mesh_match}")

        if self.session.nodes:
            self.session.dispatch(BenchEvent.NODES_FOUND)
            self.on_status(f"Búsqueda terminada: {len(self.session.nodes)} nodo(s)")
        else:
            self.session.dispatch(BenchEvent.SCAN_EMPTY)
            self.on_status("No se detectaron nodos.")
        return set(self.session.nodes)

    async def test_selected(
        self,
        mesh_id: str,
        *,
        identify_timeout: float,
        link_timeout: float,
        link_settle_seconds: float,
        response_timeout: float,
        request_approval: RequestApproval | None = None,
        request_sender: RequestSender | None = None,
        require_identity: bool = False,
        rpm_min: float | None = None,
        rpm_max: float | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
    ) -> WorkflowResult:
        """Run identify, link and telemetry acquisition for one selected node."""
        selected = normalize_mesh_id(mesh_id)
        if selected not in self.session.nodes:
            raise ValueError("Selecciona un nodo detectado")
        if self.session.state is not BenchState.SELECTING:
            raise ValueError(f"No hay un nodo listo para probar ({self.session.state.value})")

        session = self.session
        gateway = self.gateway
        session.selected_node = selected
        session.mac_address = None
        session.device_id = None
        session.telemetry = None
        session.result = None
        session.dispatch(BenchEvent.SELECT_NODE)

        await gateway.send(f"remote identify {selected}")
        self.on_status(f"TX remote identify {selected}; consultando MAC por hasta {identify_timeout:g} s…")
        async for raw in self._messages(identify_timeout, "Consulta MAC"):
            self._receive(raw)
            session.mac_address = mac_result(raw, selected)
            if session.mac_address:
                break
        self.on_status(f"MAC: {session.mac_address}" if session.mac_address else f"No se recibió MAC de {selected}.")

        await gateway.send(f"remote link {selected}")
        session.dispatch(BenchEvent.LINK_SENT)
        self.on_status(f"TX remote link {selected}; esperando confirmación LINK por hasta {link_timeout:g} s…")
        link_confirmed = False
        async for raw in self._messages(link_timeout, "Confirmación del vínculo"):
            self._receive(raw)
            outcome = link_result(raw, selected)
            if outcome is True:
                link_confirmed = True
                break
            if outcome is False:
                session.dispatch(BenchEvent.LINK_REJECTED, detail=f"El nodo {selected} respondió UNLINK")
                return WorkflowResult(session.state)
        if not link_confirmed:
            session.dispatch(BenchEvent.ERROR, detail=f"Timeout esperando [LINK] linked de {selected}")
            return WorkflowResult(session.state)

        session.dispatch(BenchEvent.LINK_CONFIRMED)
        self.on_status(f"Vínculo confirmado: {selected}")
        if link_settle_seconds:
            self.on_status(f"Estabilizando vínculo durante {link_settle_seconds:g} s…")
            await asyncio.sleep(link_settle_seconds)

        cooldown = getattr(gateway, "request_cooldown_remaining", None)
        remaining = max(0.0, float(cooldown())) if callable(cooldown) else 0.0
        advisory_check = getattr(gateway, "request_cooldown_is_advisory", None)
        advisory = bool(advisory_check()) if callable(advisory_check) else False
        approve = request_approval or _approve_request
        if not await approve(remaining, advisory):
            return WorkflowResult(session.state)

        send_request = request_sender or self._send_request
        await send_request(selected)
        session.dispatch(BenchEvent.DATA_REQUESTED)
        self.on_status(f"TX remote request {selected}; esperando telemetría por hasta {response_timeout:g} s…")
        started = time.monotonic()
        async for raw in self._messages(response_timeout, "Respuesta de telemetría"):
            self._receive(raw)
            parsed = decode_telemetry(raw)
            if parsed is None:
                continue
            received_mesh, payload = parsed
            if received_mesh != selected:
                self.on_status(f"RX omitido de {received_mesh} (nodo seleccionado {selected})")
                continue

            session.telemetry = payload
            tags = payload.get("tags", {})
            session.device_id = tags.get("device_id") if isinstance(tags, dict) else None
            session.dispatch(BenchEvent.DATA_RECEIVED)
            if require_identity and not (session.mac_address and session.device_id):
                session.dispatch(BenchEvent.IDENTITY_INCOMPLETE)
                self.on_status("Identidad incompleta: falta MAC o device-id.")
                return WorkflowResult(session.state, elapsed=time.monotonic() - started)

            passed, failures = evaluate(
                payload,
                rpm_min=rpm_min,
                rpm_max=rpm_max,
                temperature_min=temperature_min,
                temperature_max=temperature_max,
            )
            session.dispatch(BenchEvent.PASS if passed else BenchEvent.FAIL)
            session.result = "PASS" if passed else "FAIL"
            elapsed = time.monotonic() - started
            self.on_status(f"Telemetría {payload.get('schema')} de {selected} en {elapsed:.2f} s")
            self.on_status(f"Resultado: {session.result}")
            for failure in failures:
                self.on_status(f"  - {failure}")
            return WorkflowResult(session.state, failures, elapsed)

        session.dispatch(BenchEvent.ERROR, detail=f"Timeout esperando telemetría de {selected}")
        return WorkflowResult(session.state, elapsed=time.monotonic() - started)

    def _receive(self, raw: str) -> None:
        self.session.last_serial_message = raw
        self.on_message(raw)

    async def _send_request(self, mesh_id: str) -> None:
        cooldown = getattr(self.gateway, "request_cooldown_remaining", None)
        remaining = max(0.0, float(cooldown())) if callable(cooldown) else 0.0
        advisory_check = getattr(self.gateway, "request_cooldown_is_advisory", None)
        advisory = bool(advisory_check()) if callable(advisory_check) else False
        command = f"remote request {mesh_id}"
        if remaining > 0 and not advisory:
            await self.gateway.send(command, force=True)
        else:
            await self.gateway.send(command)


def decode_mesh_id(raw: str) -> str | None:
    """Return a normalized mesh ID if a gateway line contains one."""
    match = re.search(r"<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>", raw)
    return normalize_mesh_id(match.group("mesh")) if match else None


async def _approve_request(_remaining: float, _advisory: bool) -> bool:
    return True
