"""Global state machine for one roller test-bench session."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BenchState(StrEnum):
    IDLE = "inactivo"
    INITIALIZING = "inicializando"
    SCANNING = "buscando_nodos"
    NO_NODES = "sin_nodos"
    SELECTING = "seleccionando_nodo"
    LINKING = "vinculando"
    REQUESTING = "solicitando_datos"
    EVALUATING = "evaluando"
    IDENTITY_PENDING = "identidad_pendiente"
    PASSED = "aprobado"
    FAILED = "rechazado"
    ERROR = "error"


class BenchEvent(StrEnum):
    START = "iniciar"
    READY = "listo"
    SCAN_STARTED = "busqueda_iniciada"
    NODES_FOUND = "nodos_detectados"
    SCAN_EMPTY = "sin_nodos_detectados"
    SELECT_NODE = "nodo_seleccionado"
    LINK_SENT = "vinculo_enviado"
    LINK_CONFIRMED = "vinculo_confirmado"
    LINK_REJECTED = "vinculo_rechazado"
    DATA_REQUESTED = "datos_solicitados"
    DATA_RECEIVED = "datos_recibidos"
    CLEAR_TEST = "limpiar_prueba"
    PASS = "aprobado"
    FAIL = "rechazado"
    IDENTITY_INCOMPLETE = "identidad_incompleta"
    IDENTITY_COMPLETE = "identidad_completa"
    CANCEL = "cancelar"
    RESET = "reiniciar"
    ERROR = "error"


TRANSITIONS: dict[BenchState, dict[BenchEvent, BenchState]] = {
    BenchState.IDLE: {BenchEvent.START: BenchState.INITIALIZING},
    BenchState.INITIALIZING: {
        BenchEvent.READY: BenchState.SCANNING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.SCANNING: {
        BenchEvent.SCAN_STARTED: BenchState.SCANNING,
        BenchEvent.NODES_FOUND: BenchState.SELECTING,
        BenchEvent.SCAN_EMPTY: BenchState.NO_NODES,
        BenchEvent.CANCEL: BenchState.IDLE,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.NO_NODES: {
        BenchEvent.SCAN_STARTED: BenchState.SCANNING,
        BenchEvent.RESET: BenchState.SCANNING,
        BenchEvent.CANCEL: BenchState.IDLE,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.SELECTING: {
        BenchEvent.SELECT_NODE: BenchState.LINKING,
        BenchEvent.CANCEL: BenchState.SCANNING,
        BenchEvent.CLEAR_TEST: BenchState.SELECTING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.LINKING: {
        BenchEvent.LINK_SENT: BenchState.LINKING,
        BenchEvent.LINK_CONFIRMED: BenchState.REQUESTING,
        BenchEvent.LINK_REJECTED: BenchState.ERROR,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.REQUESTING: {
        BenchEvent.DATA_REQUESTED: BenchState.REQUESTING,
        BenchEvent.DATA_RECEIVED: BenchState.EVALUATING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.EVALUATING: {
        BenchEvent.PASS: BenchState.PASSED,
        BenchEvent.FAIL: BenchState.FAILED,
        BenchEvent.IDENTITY_INCOMPLETE: BenchState.IDENTITY_PENDING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.IDENTITY_PENDING: {
        BenchEvent.IDENTITY_COMPLETE: BenchState.EVALUATING,
        BenchEvent.CLEAR_TEST: BenchState.SELECTING,
        BenchEvent.RESET: BenchState.SCANNING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.PASSED: {
        BenchEvent.RESET: BenchState.SCANNING,
        BenchEvent.CLEAR_TEST: BenchState.SELECTING,
        BenchEvent.DATA_REQUESTED: BenchState.REQUESTING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.FAILED: {
        BenchEvent.RESET: BenchState.SCANNING,
        BenchEvent.CLEAR_TEST: BenchState.SELECTING,
        BenchEvent.DATA_REQUESTED: BenchState.REQUESTING,
        BenchEvent.ERROR: BenchState.ERROR,
    },
    BenchState.ERROR: {
        BenchEvent.RESET: BenchState.SCANNING,
        BenchEvent.CLEAR_TEST: BenchState.SELECTING,
        BenchEvent.CANCEL: BenchState.IDLE,
        BenchEvent.DATA_REQUESTED: BenchState.REQUESTING,
    },
}


@dataclass(slots=True)
class BenchSession:
    state: BenchState = BenchState.IDLE
    nodes: set[str] = field(default_factory=set)
    node_rssi: dict[str, int] = field(default_factory=dict)
    selected_node: str | None = None
    mac_address: str | None = None
    device_id: str | None = None
    telemetry: dict[str, Any] | None = None
    result: str | None = None
    error: str | None = None
    last_serial_message: str | None = None
    link_confirmed: bool = False

    def dispatch(self, event: BenchEvent, *, detail: str | None = None) -> BenchState:
        try:
            next_state = TRANSITIONS[self.state][event]
        except KeyError as exc:
            raise ValueError(f"Evento '{event.value}' no válido en estado '{self.state.value}'") from exc
        self.state = next_state
        if event is BenchEvent.ERROR:
            self.error = detail or "Error sin detalle"
        if event is BenchEvent.LINK_REJECTED:
            self.error = detail or "El nodo rechazó el vínculo"
            self.link_confirmed = False
        if event is BenchEvent.LINK_CONFIRMED:
            self.link_confirmed = True
        if event is BenchEvent.DATA_REQUESTED:
            self.result = None
            self.error = None
        if event is BenchEvent.CLEAR_TEST:
            self.selected_node = None
            self.mac_address = None
            self.device_id = None
            self.telemetry = None
            self.result = None
            self.error = None
            self.last_serial_message = None
            self.link_confirmed = False
        if event is BenchEvent.RESET:
            self.nodes.clear()
            self.node_rssi.clear()
            self.selected_node = None
            self.mac_address = None
            self.device_id = None
            self.telemetry = None
            self.result = None
            self.error = None
            self.last_serial_message = None
            self.link_confirmed = False
        return self.state
