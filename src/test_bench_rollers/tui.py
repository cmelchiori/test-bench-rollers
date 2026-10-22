"""Textual terminal interface for operating the roller bench."""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Select, Static, TabbedContent, TabPane
from rich.text import Text

from .cli import HARDWARE_ALERT_TEMP_C, evaluate
from .remote_console import RemoteConsoleServer
from .serial_gateway import (
    SerialGateway,
    decode_telemetry,
    link_result,
    mac_result,
    normalize_mesh_id,
)
from .state_machine import BenchEvent, BenchSession, BenchState


class ConfirmUnlinkScreen(ModalScreen[bool]):
    """Touch-sized confirmation before unlinking the full mesh group."""

    CSS = """
    ConfirmUnlinkScreen { align: center middle; background: $background 70%; }
    #confirm-dialog { width: 64; height: auto; max-height: 15; padding: 2; border: round $warning; background: $surface; }
    #confirm-title { height: 2; text-style: bold; color: $warning; }
    #confirm-copy { height: auto; min-height: 3; margin-bottom: 1; }
    #confirm-buttons { height: 5; }
    #confirm-buttons Button { width: 1fr; height: 4; margin-right: 1; text-style: bold; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static("Desvincular todos los nodos", id="confirm-title")
            yield Static(
                "Se enviará remote unlink group a toda la red Mesh. ¿Quieres continuar?",
                id="confirm-copy",
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("CANCELAR", id="cancel-unlink")
                yield Button("CONFIRMAR UNLINK", id="confirm-unlink", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-unlink":
            self.dismiss(True)
        elif event.button.id == "cancel-unlink":
            self.dismiss(False)


class BenchTuiApp(App[None]):
    """Interactive TUI; serial operations run as Textual workers."""

    TITLE = "Roller Test Bench"
    SUB_TITLE = "CONTROL DE NODOS MESH · BANCO DE RODILLOS"
    BINDINGS = [
        ("s", "scan", "Buscar"),
        ("t", "test_node", "Probar nodo"),
        ("u", "unlink_group", "Unlink grupo"),
        ("q", "quit_app", "Salir"),
    ]
    CSS = """
    Screen { layout: vertical; background: #0d0f12; }
    #app-header { height: 2; padding: 0 2; border-bottom: solid #292d33; background: #14171b; }
    #brand { width: 18; height: 2; content-align: left middle; color: #29d3bd; text-style: bold; }
    #app-title { width: 1fr; height: 2; content-align: left middle; color: #dfe5e9; text-style: bold; }
    #global-status { width: 38; height: 2; padding: 0 1; content-align: right middle; color: #a8b3bf; }
    #connection-info { height: 1; background: #111317; }
    #connection { width: 1fr; height: 1; padding: 0 2; content-align: left middle; color: #788596; }
    #remote-status { width: 1fr; height: 1; padding: 0 2; content-align: right middle; color: #a8b3bf; }
    #main-tabs { height: 1fr; }
    TabPane { padding: 1 2; }
    TabbedContent > ContentTabs { height: 3; background: #111317; }
    TabbedContent > ContentTabs Tab { min-width: 16; height: 3; content-align: center middle; }
    #dashboard { height: 1fr; }
    #hero { layout: horizontal; height: 10; min-height: 9; padding: 1 2; border: round #292d33; background: #111317; }
    #hero-copy-panel { width: 1fr; height: 1fr; }
    #hero-actions { width: 46; min-width: 36; height: 1fr; margin-left: 2; }
    #hero-action-row-1, #hero-action-row-2 { height: 1fr; }
    #hero-action-row-1 Button, #hero-action-row-2 Button {
        width: 1fr; height: 3; margin-left: 1; min-width: 15; text-style: bold;
    }
    #eyebrow { height: 1; color: #29d3bd; text-style: bold; }
    #dashboard-title { height: 2; content-align: left middle; text-style: bold; color: #e5e8eb; }
    #hero-copy { height: auto; color: #8290a2; }
    #workspace { height: 1fr; }
    #node-panel { width: 28%; min-width: 28; height: 1fr; padding: 1 2; border: round #292d33; background: #111317; }
    #node-heading { height: 1; color: #8290a2; text-style: bold; }
    #auto-toggle { width: 1fr; height: 3; margin-top: 1; text-style: bold; }
    #detail-panel { width: 1fr; height: 1fr; margin-left: 1; }
    #state { height: auto; min-height: 2; padding: 0 1; color: #8290a2; }
    #home-summary { height: auto; min-height: 1; max-height: 4; padding: 0 1; color: #8290a2; }
    #scan-nodes { height: 1; color: #8290a2; }
    #node-select { width: 1fr; height: 4; margin: 1 0; }
    #metrics { height: 1fr; min-height: 12; padding: 1 2; border: round #292d33; background: #111317; color: #dfe5e9; }
    #identity-actions { height: 5; display: none; }
    #identity-actions Button { width: 1fr; height: 4; text-style: bold; }
    #debug-pane { height: 1fr; }
    #debug-description { height: 2; color: #8290a2; }
    #serial-log { height: 1fr; min-height: 5; border: round #292d33; background: #111317; }
    """

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        gateway_factory: type[SerialGateway] = SerialGateway,
    ) -> None:
        super().__init__()
        self.args = args
        self.session = BenchSession()
        self._gateway_factory = gateway_factory
        self.gateway: SerialGateway | None = None
        self._busy = False
        self._activity: str | None = None
        self._deadline = 0.0
        self._started_at = 0.0
        self._unlink_message = "Listo para enviar el comando."
        self._remote_active = False
        self._remote_client: str | None = None
        self._auto_enabled = False
        self._auto_running = False
        self._auto_stop_requested = False
        self.remote_console: RemoteConsoleServer | None = None
        self._last_remote_status: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-header"):
            yield Static("QA BENCH", id="brand")
            yield Static("ROLLER TEST BENCH", id="app-title")
            yield Static("Estado: preparando…", id="global-status")
        with Horizontal(id="connection-info"):
            yield Static(f"UART · {self.args.port} @ {self.args.baudrate}", id="connection")
            yield Static("Consola remota iniciando…", id="remote-status")
        with TabbedContent(initial="operation-tab", id="main-tabs"):
            with TabPane("Operación", id="operation-tab"):
                with Vertical(id="dashboard"):
                    with Horizontal(id="hero"):
                        with Vertical(id="hero-copy-panel"):
                            yield Static("Q A   B E N C H", id="eyebrow")
                            yield Static("Prueba individual de nodos", id="dashboard-title")
                            yield Static(
                                "Selección de nodo, RPM, temperaturas y alertas de hardware.",
                                id="hero-copy",
                            )
                        with Vertical(id="hero-actions"):
                            with Horizontal(id="hero-action-row-1"):
                                yield Button("BUSCAR NODOS", id="scan", variant="primary")
                                yield Button("LIMPIAR LOG", id="clear-log")
                            with Horizontal(id="hero-action-row-2"):
                                yield Button("UNLINK GROUP", id="unlink", variant="error")
                                yield Button("REQUEST NODE", id="test", variant="success")
                    with Horizontal(id="workspace"):
                        with Vertical(id="node-panel"):
                            yield Static("N O D O S", id="node-heading")
                            yield Static("Nodos: ninguno", id="scan-nodes")
                            yield Select([], prompt="Primero busca nodos", allow_blank=True, id="node-select")
                            yield Button("INICIAR PRUEBA AUTO", id="auto-toggle")
                            yield Static("Esperando búsqueda…", id="state")
                        with Vertical(id="detail-panel"):
                            yield Static(
                                "Sin telemetría. Busca un nodo para comenzar.",
                                id="home-summary",
                                markup=False,
                            )
                            yield Static(
                                "MAC: —\nDevice-ID: —\nRPM: —\nTemperaturas: —\nAlarmas: —\nResultado: —",
                                id="metrics",
                                markup=False,
                            )
                    with Horizontal(id="identity-actions"):
                        yield Button("REINTENTAR IDENTIDAD", id="retry-identity", variant="warning")
            with TabPane("Depuración", id="debug-tab"):
                with Vertical(id="debug-pane"):
                    yield Static("Registro serial de solo consulta para diagnóstico.", id="debug-description")
                    yield RichLog(id="serial-log", wrap=True, markup=False, max_lines=1000)

    async def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(0.25, self._refresh_status)
        try:
            self.remote_console = RemoteConsoleServer(
                port=getattr(self.args, "remote_port", 8765),
                gateway_provider=lambda: self.gateway,
                status_provider=self._remote_status,
                acquire=self._acquire_remote,
                release=self._release_remote,
                confirm_command=self._confirm_remote_command,
                log=self._log,
            )
            await self.remote_console.start()
            self.query_one("#remote-status", Static).update(
                f"REMOTE · 127.0.0.1:{self.remote_console.bound_port} · SSH"
            )
        except (OSError, ValueError) as exc:
            self.query_one("#remote-status", Static).update(f"REMOTE · no disponible: {exc}")
        self.run_worker(self._connect(), name="serial-connect", group="serial", exit_on_error=False)

    async def on_unmount(self) -> None:
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if self.remote_console is not None:
            await self.remote_console.close()
            self.remote_console = None
        if self.gateway is not None:
            await self.gateway.__aexit__(None, None, None)
            self.gateway = None

    async def _connect(self) -> None:
        try:
            gateway = self._gateway_factory(self.args.port, self.args.baudrate)
            await gateway.__aenter__()
            self.gateway = gateway
            self.query_one("#connection", Static).update(
                f"UART · {self.args.port} @ {self.args.baudrate} · conectado"
            )
            self._log(f"Puerto serial abierto: {self.args.port} @ {self.args.baudrate} baud")
            self._refresh_status()
        except (OSError, RuntimeError) as exc:
            if self.session.state is BenchState.IDLE:
                self.session.dispatch(BenchEvent.START)
            self.session.dispatch(BenchEvent.ERROR, detail=str(exc))
            self.query_one("#connection", Static).update(f"Error serial: {exc}")
            self._refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._remote_active:
            return
        if event.button.id == "clear-log":
            self.query_one("#serial-log", RichLog).clear()
            return
        if event.button.id == "auto-toggle":
            self._toggle_automated_test()
            return
        if event.button.id == "test" and self._auto_mode_enabled():
            self._start_action(self._run_automated_test(), "automated-test")
            return
        actions = {
            "scan": (self._scan, "scan"),
            "test": (self._test_selected, "test"),
            "retry-identity": (self._retry_identity, "retry-identity"),
            "unlink": (self._unlink_group, "unlink"),
        }
        if event.button.id == "quit":
            self.exit()
            return
        if event.button.id == "unlink":
            self.push_screen(ConfirmUnlinkScreen(), callback=self._unlink_confirmation_result)
            return
        if event.button.id not in actions:
            return
        worker, name = actions[event.button.id]
        self._set_busy(True)
        self.run_worker(worker(), name=name, group="operation", exclusive=True, exit_on_error=False)

    def _toggle_automated_test(self) -> None:
        if self._auto_enabled:
            self._set_auto_enabled(False)
            self._auto_stop_requested = True
            self._update_visible(
                "home-summary",
                "Deteniendo prueba automatizada; no se enviarán más solicitudes.",
            )
            self._refresh_status()
            return
        if self._remote_active or self._busy:
            return
        if self.session.state is BenchState.IDENTITY_PENDING:
            self._update_visible(
                "home-summary",
                "Completa primero la identidad del nodo para activar la prueba automatizada.",
            )
            return
        self._set_auto_enabled(True)
        if self.session.state in {BenchState.SELECTING, BenchState.PASSED, BenchState.FAILED}:
            self._start_action(self._run_automated_test(), "automated-test")
        else:
            self._update_visible(
                "home-summary",
                "Prueba automatizada activada. Busca un nodo para comenzar.",
            )

    def _unlink_confirmation_result(self, confirmed: bool | None) -> None:
        if confirmed:
            self._start_action(self._unlink_group(), "unlink")
        else:
            self._log("Unlink group cancelado por el operador")

    def action_scan(self) -> None:
        self._start_action(self._scan(), "scan")

    def action_test_node(self) -> None:
        if self._auto_mode_enabled():
            self._start_action(self._run_automated_test(), "automated-test")
        else:
            self._start_action(self._test_selected(), "test")

    def action_unlink_group(self) -> None:
        if not self._remote_active:
            self.push_screen(ConfirmUnlinkScreen(), callback=self._unlink_confirmation_result)

    def action_quit_app(self) -> None:
        if not self._remote_active:
            self.exit()

    def _start_action(self, worker: Any, name: str) -> None:
        if self._busy or self._remote_active:
            return
        self._set_busy(True)
        self.run_worker(worker, name=name, group="operation", exclusive=True, exit_on_error=False)

    async def _scan(self) -> None:
        try:
            if self.gateway is None:
                raise RuntimeError("El puerto serial no está conectado")
            if self.session.state is BenchState.IDLE:
                self.session.dispatch(BenchEvent.START)
                self.session.dispatch(BenchEvent.READY)
            elif self.session.state in {
                BenchState.PASSED,
                BenchState.FAILED,
                BenchState.ERROR,
                BenchState.IDENTITY_PENDING,
            }:
                self.session.dispatch(BenchEvent.RESET)
            elif self.session.state is BenchState.SELECTING:
                self.session.dispatch(BenchEvent.CANCEL)
            elif self.session.state is not BenchState.SCANNING:
                raise RuntimeError(f"No se puede buscar durante {self.session.state.value}")

            self.session.nodes.clear()
            self.session.selected_node = None
            self.session.telemetry = None
            self.session.result = None
            self.session.mac_address = None
            self.session.device_id = None
            self.query_one("#identity-actions", Horizontal).display = False
            self._render_home_summary()
            self.session.dispatch(BenchEvent.SCAN_STARTED)
            await self.gateway.send("remote scan group")
            self._log("TX remote scan group")
            self._begin_activity("Búsqueda de nodos", self.args.scan_seconds)
            self._refresh_scan_nodes()
            async for raw in self.gateway.messages(self.args.scan_seconds):
                self.session.last_serial_message = raw
                self._log(f"RX {raw}")
                match = re.search(r"<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>", raw)
                if match:
                    mesh_id = normalize_mesh_id(match.group("mesh"))
                    if mesh_id not in self.session.nodes:
                        self.session.nodes.add(mesh_id)
                        self._log(f"Nodo detectado: {mesh_id}")
                        self._refresh_scan_nodes()
                self._refresh_status()
            if self.session.nodes:
                self.session.dispatch(BenchEvent.NODES_FOUND)
                selector = self.query_one("#node-select", Select)
                selector.set_options((mesh_id, mesh_id) for mesh_id in sorted(self.session.nodes))
                selector.value = sorted(self.session.nodes)[0]
                self._log(f"Búsqueda terminada: {len(self.session.nodes)} nodo(s)")
                self._render_home_summary()
                if len(self.session.nodes) == 1:
                    self._log(f"Un solo nodo detectado ({selector.value}); inicio la prueba automáticamente")
                    if self._auto_mode_enabled():
                        await self._run_automated_test(manage_busy=False)
                    else:
                        await self._test_selected(manage_busy=False)
            else:
                self._log("Búsqueda terminada: no se detectaron nodos")
            self._refresh_scan_nodes()
            self._render_home_summary()
        except (OSError, ValueError, RuntimeError) as exc:
            self._set_error(str(exc))
        finally:
            self._end_activity()
            self._set_busy(False)

    async def _test_selected(self, *, manage_busy: bool = True) -> None:
        try:
            if self.gateway is None:
                raise RuntimeError("El puerto serial no está conectado")
            selected = self.query_one("#node-select", Select).value
            if not isinstance(selected, str) or selected not in self.session.nodes:
                raise ValueError("Selecciona un nodo detectado")
            if self.session.state is not BenchState.SELECTING:
                raise ValueError(f"No hay un nodo listo para probar ({self.session.state.value})")
            if self._request_wait_seconds() > 0:
                remaining = self._request_wait_seconds()
                self._update_visible(
                    "home-summary",
                    f"Solicitud disponible en {remaining:.0f} s desde la última respuesta exitosa.",
                )
                self._refresh_status()
                return

            self.session.selected_node = selected
            self.session.mac_address = None
            self.session.device_id = None
            self.session.telemetry = None
            self.session.result = None
            self.query_one("#identity-actions", Horizontal).display = False
            self.session.dispatch(BenchEvent.SELECT_NODE)

            identify_timeout = getattr(self.args, "identify_timeout", 5.0)
            await self._identify_mac(selected, identify_timeout)
            self._update_identity_metrics()

            await self.gateway.send(f"remote link {selected}")
            self.session.dispatch(BenchEvent.LINK_SENT)
            self._log(f"TX remote link {selected}")
            self._begin_activity("Esperando confirmación LINK", self.args.link_timeout)
            linked = False
            async for raw in self.gateway.messages(self.args.link_timeout):
                self.session.last_serial_message = raw
                self._log(f"RX {raw}")
                outcome = link_result(raw, selected)
                if outcome is True:
                    linked = True
                    break
                if outcome is False:
                    self.session.dispatch(BenchEvent.LINK_REJECTED, detail=f"El nodo {selected} respondió UNLINK")
                    self._refresh_status()
                    return
            if not linked:
                self._set_error(f"Timeout esperando confirmación LINK de {selected}")
                return

            self.session.dispatch(BenchEvent.LINK_CONFIRMED)
            if self.args.link_settle_seconds:
                self._begin_activity("Estabilizando vínculo", self.args.link_settle_seconds)
                await asyncio.sleep(self.args.link_settle_seconds)

            await self.gateway.send(f"remote request {selected}")
            self.session.dispatch(BenchEvent.DATA_REQUESTED)
            self._log(f"TX remote request {selected}")
            self._begin_activity("Esperando telemetría", self.args.response_timeout)
            async for raw in self.gateway.messages(self.args.response_timeout):
                self.session.last_serial_message = raw
                self._log(f"RX {raw}")
                parsed = decode_telemetry(raw)
                if parsed is None:
                    continue
                mesh_id, payload = parsed
                if mesh_id != selected:
                    continue
                self.session.telemetry = payload
                tags = payload.get("tags", {})
                self.session.device_id = tags.get("device_id") if isinstance(tags, dict) else None
                self.session.dispatch(BenchEvent.DATA_RECEIVED)
                if not self._identity_complete():
                    self.session.dispatch(BenchEvent.IDENTITY_INCOMPLETE)
                    self._show_identity_pending()
                    self._log("Resultado pendiente: falta MAC o device-id. Reintenta identidad.")
                    self._refresh_status()
                    return
                self._apply_quality_result()
                self._log(f"Prueba {self.session.result}: {selected}")
                return
            self._set_error(f"Timeout esperando telemetría de {selected}")
        except (OSError, ValueError, RuntimeError) as exc:
            self._set_error(str(exc))
        finally:
            self._end_activity()
            if manage_busy:
                self._set_busy(False)

    def _auto_mode_enabled(self) -> bool:
        return self._auto_enabled

    def _set_auto_enabled(self, enabled: bool) -> None:
        self._auto_enabled = enabled
        if enabled:
            self._auto_stop_requested = False
        button = self.query_one("#auto-toggle", Button)
        button.label = "DETENER PRUEBA AUTO" if enabled else "INICIAR PRUEBA AUTO"
        button.variant = "error" if enabled else "default"

    async def _run_automated_test(self, *, manage_busy: bool = True) -> None:
        if self._auto_running:
            return
        self._auto_running = True
        self.query_one("#auto-toggle", Button).disabled = False
        if manage_busy:
            self._set_busy(True)
        try:
            if self.session.state is BenchState.SELECTING:
                await self._test_selected(manage_busy=False)
            elif self.session.state not in {BenchState.PASSED, BenchState.FAILED}:
                self._update_visible(
                    "home-summary",
                    "Selecciona un nodo listo para iniciar la prueba automatizada.",
                )
                self._set_auto_enabled(False)
                return

            if self.session.state not in {BenchState.PASSED, BenchState.FAILED}:
                self._set_auto_enabled(False)
                return
            if not self._identity_complete():
                self._update_visible(
                    "home-summary",
                    "Completa la identidad del nodo antes de iniciar la prueba automatizada.",
                )
                self._set_auto_enabled(False)
                return

            while (
                self._auto_mode_enabled()
                and self.session.state in {BenchState.PASSED, BenchState.FAILED}
            ):
                if not await self._wait_for_next_automated_request():
                    break
                if not self._auto_mode_enabled():
                    break
                if not await self._request_selected_again():
                    self._set_auto_enabled(False)
                    break
        finally:
            self._auto_running = False
            self._end_activity()
            if self._auto_stop_requested:
                self._update_visible("home-summary", "Prueba automatizada detenida.")
                self._auto_stop_requested = False
            if manage_busy:
                self._set_busy(False)

    async def _wait_for_next_automated_request(self) -> bool:
        remaining = self._request_wait_seconds()
        if remaining <= 0:
            return self._auto_mode_enabled()
        self._begin_activity("Esperando intervalo para el próximo request", remaining)
        self._update_visible(
            "home-summary",
            f"Prueba automatizada activa · próxima solicitud en {remaining:.0f} s.",
        )
        while self._auto_mode_enabled():
            remaining = self._request_wait_seconds()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(0.25, remaining))
        return False

    async def _request_selected_again(self) -> bool:
        selected = self.session.selected_node
        if self.gateway is None or selected is None:
            self._set_error("No hay un nodo seleccionado para la solicitud automatizada")
            return False
        try:
            await self.gateway.send(f"remote request {selected}")
            self.session.result = None
            self.session.dispatch(BenchEvent.DATA_REQUESTED)
            self._render_metrics()
            self._log(f"TX remote request {selected} (prueba automatizada)")
            self._begin_activity("Esperando telemetría automatizada", self.args.response_timeout)
            async for raw in self.gateway.messages(self.args.response_timeout):
                self.session.last_serial_message = raw
                self._log(f"RX {raw}")
                parsed = decode_telemetry(raw)
                if parsed is None or parsed[0] != selected:
                    continue
                self.session.telemetry = parsed[1]
                tags = parsed[1].get("tags", {})
                self.session.device_id = tags.get("device_id") if isinstance(tags, dict) else None
                self.session.dispatch(BenchEvent.DATA_RECEIVED)
                if not self._identity_complete():
                    self.session.dispatch(BenchEvent.IDENTITY_INCOMPLETE)
                    self._show_identity_pending()
                    self._log("Prueba automatizada detenida: identidad incompleta")
                    return False
                self._apply_quality_result()
                self._log(f"Prueba automatizada {self.session.result}: {selected}")
                return True
            self._set_error(f"Timeout esperando telemetría automatizada de {selected}")
        except (OSError, ValueError, RuntimeError) as exc:
            self._set_error(str(exc))
        finally:
            self._end_activity()
        return False

    async def _identify_mac(self, mesh_id: str, timeout: float) -> None:
        if self.session.mac_address:
            return
        assert self.gateway is not None
        await self.gateway.send(f"remote identify {mesh_id}")
        self._log(f"TX remote identify {mesh_id}")
        self._begin_activity("Consultando MAC con identify", timeout)
        async for raw in self.gateway.messages(timeout):
            self.session.last_serial_message = raw
            self._log(f"RX {raw}")
            address = mac_result(raw, mesh_id)
            if address:
                self.session.mac_address = address
                break
            self._refresh_status()
        if self.session.mac_address:
            self._log(f"MAC identificada: {self.session.mac_address}")
        else:
            self._log(f"No se recibió MAC de {mesh_id}")

    async def _retry_identity(self) -> None:
        try:
            if self.gateway is None:
                raise RuntimeError("El puerto serial no está conectado")
            if self.session.state is not BenchState.IDENTITY_PENDING or not self.session.selected_node:
                raise ValueError("No hay una identidad pendiente de reintento")
            selected = self.session.selected_node
            await self._identify_mac(selected, getattr(self.args, "identify_timeout", 5.0))
            if not self.session.device_id:
                remaining = self._request_wait_seconds()
                if remaining > 0:
                    self._update_visible(
                        "home-summary",
                        f"Falta device-id. Reintento de telemetría disponible en {remaining:.0f} s.",
                    )
                    self._refresh_status()
                    return
                await self._request_identity_telemetry(
                    selected, getattr(self.args, "response_timeout", 10.0)
                )
            if self._identity_complete():
                self.session.dispatch(BenchEvent.IDENTITY_COMPLETE)
                self._apply_quality_result()
            else:
                self._show_identity_pending()
                self._log("La identidad sigue incompleta; vuelve a intentarlo cuando el nodo esté disponible.")
                self._refresh_status()
        except (OSError, ValueError, RuntimeError) as exc:
            self._set_error(str(exc))
        finally:
            self._end_activity()
            self._set_busy(False)

    async def _request_identity_telemetry(self, mesh_id: str, timeout: float) -> bool:
        assert self.gateway is not None
        await self.gateway.send(f"remote request {mesh_id}")
        self._log(f"TX remote request {mesh_id} (reintento de device-id)")
        self._begin_activity("Reintentando consulta de device-id", timeout)
        async for raw in self.gateway.messages(timeout):
            self.session.last_serial_message = raw
            self._log(f"RX {raw}")
            parsed = decode_telemetry(raw)
            if parsed is None:
                continue
            received_mesh_id, payload = parsed
            if received_mesh_id != mesh_id:
                continue
            self.session.telemetry = payload
            tags = payload.get("tags", {})
            self.session.device_id = tags.get("device_id") if isinstance(tags, dict) else None
            self._render_metrics()
            return bool(self.session.device_id)
        self._log(f"Timeout esperando telemetría de {mesh_id} durante reintento")
        return False

    def _identity_complete(self) -> bool:
        return bool(self.session.mac_address and self.session.device_id)

    def _show_identity_pending(self) -> None:
        self.session.result = None
        self.query_one("#identity-actions", Horizontal).display = True
        self.query_one("#home-summary", Static).update(
            "IDENTIDAD INCOMPLETA · falta MAC o device-id. Reintenta para completar la prueba."
        )
        self._render_metrics()
        self._set_busy(self._busy)

    def _apply_quality_result(self) -> None:
        if self.session.telemetry is None:
            raise RuntimeError("No hay telemetría disponible para evaluar")
        passed, failures = evaluate(
            self.session.telemetry,
            rpm_min=self.args.rpm_min,
            rpm_max=self.args.rpm_max,
            temperature_min=self.args.temperature_min,
            temperature_max=self.args.temperature_max,
        )
        self.session.dispatch(BenchEvent.PASS if passed else BenchEvent.FAIL)
        self.session.result = "PASS" if passed else "FAIL"
        self._render_metrics(failures)
        self._render_home_summary()
        self._set_busy(self._busy)
        self._refresh_status()

    async def _unlink_group(self) -> None:
        try:
            if self.gateway is None:
                raise RuntimeError("El puerto serial no está conectado")
            await self.gateway.send("remote unlink group")
            self._log("TX remote unlink group")
            listen_seconds = getattr(self.args, "unlink_listen_seconds", 3.0)
            self._log(f"Esperando actividad serial durante {listen_seconds:g} s…")
            self._begin_activity("Procesando remote unlink group", listen_seconds)
            self._unlink_message = f"Comando enviado. Escuchando actividad serial por {listen_seconds:g} s…"
            self._update_visible("home-summary", self._unlink_message)
            received = False
            async for raw in self.gateway.messages(listen_seconds):
                received = True
                self.session.last_serial_message = raw
                self._log(f"RX {raw}")
                self._unlink_message = f"Actividad recibida:\n{raw}"
                self._update_visible("home-summary", self._unlink_message)
                self._refresh_status()
            if not received:
                self._log("Sin mensajes RX durante la ventana de escucha")
                self._unlink_message = "Sin mensajes RX durante la ventana de escucha."
            else:
                self._unlink_message = "Procesamiento terminado. Consulta el registro para ver todos los mensajes."
            self._update_visible("home-summary", self._unlink_message)
            self._log("Procesamiento de unlink group terminado")
        except (OSError, RuntimeError) as exc:
            self._set_error(f"Error al procesar unlink group: {exc}")
        finally:
            self._end_activity()
            self._set_busy(False)

    def _update_identity_metrics(self) -> None:
        self._render_metrics()

    def _set_error(self, detail: str) -> None:
        if self.session.state not in {BenchState.ERROR, BenchState.PASSED, BenchState.FAILED}:
            self.session.dispatch(BenchEvent.ERROR, detail=detail)
        else:
            self.session.error = detail
        self._log(f"ERROR {detail}")
        self._refresh_status()

    def _log(self, message: str) -> None:
        try:
            self.query_one("#serial-log", RichLog).write(message)
        except NoMatches:
            pass
        if self.remote_console is not None:
            asyncio.create_task(self.remote_console.publish({"type": "log", "message": message}))

    def _begin_activity(self, label: str, seconds: float) -> None:
        self._activity = label
        self._started_at = time.monotonic()
        self._deadline = self._started_at + seconds
        self._refresh_status()

    def _end_activity(self) -> None:
        self._activity = None
        self._refresh_status()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget_id in ("scan", "unlink", "retry-identity", "clear-log"):
            self.query_one(f"#{widget_id}", Button).disabled = busy or self._remote_active
        self.query_one("#test", Button).disabled = (
            busy
            or self._remote_active
            or self.session.state is not BenchState.SELECTING
            or self._request_wait_seconds() > 0
        )
        self.query_one("#auto-toggle", Button).disabled = (
            self._remote_active or (busy and not self._auto_running)
        )
        self._refresh_request_button()
        self.query_one("#identity-actions", Horizontal).display = (
            self.session.state is BenchState.IDENTITY_PENDING
        )

    def _refresh_scan_nodes(self) -> None:
        nodes = sorted(self.session.nodes)
        display = f"Nodos detectados: {', '.join(nodes)}" if nodes else "Nodos: ninguno"
        self._update_visible("scan-nodes", display)

    def _render_home_summary(self) -> None:
        if self.session.result:
            summary = "Prueba completada. El resultado y sus detalles aparecen abajo."
        elif self.session.nodes:
            summary = f"{len(self.session.nodes)} nodo(s) detectado(s). Selecciona uno y solicita la prueba."
        else:
            summary = "Sin telemetría. Busca un nodo para comenzar."
        self._update_visible("home-summary", summary)

    def _render_metrics(self, failures: list[str] | None = None) -> None:
        selected = self.session.selected_node or "Sin nodo seleccionado"
        content = Text()

        def add_line(label: str = "", value: str = "", *, value_style: str | None = None) -> None:
            if content.plain:
                content.append("\n")
            if label:
                content.append(label, style="bold")
            if value:
                content.append(value, style=value_style)

        add_line("Nodo seleccionado: ", selected)
        add_line("Device ID: ", self.session.device_id or "—")
        add_line("MAC: ", self.session.mac_address or "No recibida")
        payload = self.session.telemetry
        if payload:
            fields = payload.get("fields", {})
            temperatures = fields.get("temperature", [])
            if not isinstance(temperatures, list):
                temperatures = [temperatures]
            temperature_lines = [
                f"Canal {index}: {value} °C" if value is not None else f"Canal {index}: Sin lectura"
                for index, value in enumerate(temperatures, start=1)
            ]
            result = self.session.result or (
                "PENDIENTE DE IDENTIDAD"
                if self.session.state is BenchState.IDENTITY_PENDING
                else "SOLICITANDO"
                if self.session.state is BenchState.REQUESTING
                else "EVALUANDO"
                if self.session.state is BenchState.EVALUATING
                else "EN ESPERA"
            )
            link_state = (
                "linked"
                if self.session.state
                in {BenchState.PASSED, BenchState.FAILED, BenchState.IDENTITY_PENDING}
                else "—"
            )
            rpm_range = self._format_range(
                getattr(self.args, "rpm_min", None),
                getattr(self.args, "rpm_max", None),
                unit="RPM",
            )
            temperature_range = self._format_range(
                getattr(self.args, "temperature_min", None),
                getattr(self.args, "temperature_max", None),
                unit="°C",
            )
            result_style = {
                "PASS": "bold green",
                "FAIL": "bold red",
            }.get(result, "bold yellow")
            add_line("Resultado QA: ", result, value_style=result_style)
            add_line("Criterios aplicados:")
            add_line(value=f"  - RPM > 0{rpm_range}")
            add_line(value=f"  - Temperaturas válidas y ≤ {HARDWARE_ALERT_TEMP_C} °C{temperature_range}")
            add_line(value="  - Sin alarmas latched activas")
            add_line("Telemetría")
            add_line("RPM: ", f"{fields.get('rpm', '—')} RPM")
            add_line("Respuesta: ", "recibida")
            add_line("Conexión: ", "Online" if self.gateway else "Offline")
            add_line("Link: ", link_state)
            add_line("Temperaturas")
            for temperature in temperature_lines or ["Sin lecturas de temperatura"]:
                add_line(value=temperature)
            add_line("Alarmas: ", str(fields.get("alarms", "—")))
            if failures:
                add_line("Fallas:")
                for failure in failures:
                    add_line(value=f"  - {failure}")
        self.query_one("#metrics", Static).update(content)

    @staticmethod
    def _format_range(minimum: float | None, maximum: float | None, *, unit: str) -> str:
        if minimum is None and maximum is None:
            return ""
        lower = f"≥ {minimum:g}" if minimum is not None else "sin mínimo"
        upper = f"≤ {maximum:g}" if maximum is not None else "sin máximo"
        return f" (configurado: {lower} / {upper} {unit})"

    def _update_visible(self, widget_id: str, value: str) -> None:
        self.query_one(f"#{widget_id}", Static).update(value)

    def _status_text(self) -> str:
        state = f"Estado: {self.session.state.value}"
        if self._remote_active:
            state += f"\nBANCO BLOQUEADO POR ACCESO REMOTO: {self._remote_client or 'consola remota'}"
        if self.session.selected_node:
            state += f"\nNodo: {self.session.selected_node}"
        if self.session.result:
            state += f"\nResultado: {self.session.result}"
        if self.session.error:
            state += f"\nError: {self.session.error}"
        if (remaining := self._request_wait_seconds()) > 0:
            state += f"\nPróximo request en {remaining:.0f} s"
        if self._activity:
            elapsed = min(time.monotonic() - self._started_at, self._deadline - self._started_at)
            remaining = max(0.0, self._deadline - time.monotonic())
            state += f"\n{self._activity}: {elapsed:.0f} s transcurridos, {remaining:.0f} s restantes"
        return state

    def _refresh_status(self) -> None:
        if not self.is_mounted:
            return
        state = self._status_text()
        try:
            self._refresh_request_button()
            self.query_one("#global-status", Static).update(self._header_status_text())
            self._update_visible("state", state)
        except NoMatches:
            # The timer can tick between the screen tree being removed and app unmount.
            return
        remote_status = self._remote_status()
        if remote_status != self._last_remote_status:
            self._last_remote_status = remote_status
            if self.remote_console is not None:
                asyncio.create_task(
                    self.remote_console.publish({"type": "status", "status": remote_status})
                )

    def _request_wait_seconds(self) -> float:
        if self.gateway is None:
            return 0.0
        cooldown = getattr(self.gateway, "request_cooldown_remaining", None)
        return max(0.0, float(cooldown())) if callable(cooldown) else 0.0

    def _header_status_text(self) -> str:
        if self._remote_active:
            return "UART bajo control remoto"
        if self._auto_running and not self._auto_enabled:
            return "Deteniendo prueba auto"
        if self._activity:
            return self._activity
        if (remaining := self._request_wait_seconds()) > 0:
            return f"Request en {remaining:.0f} s"
        return f"Estado: {self.session.state.value}"

    def _refresh_request_button(self) -> None:
        try:
            button = self.query_one("#test", Button)
        except NoMatches:
            return
        remaining = self._request_wait_seconds()
        if remaining > 0:
            seconds = int(remaining + 0.999)
            button.label = f"REQUEST NODE · {seconds}s"
            button.disabled = True
        else:
            button.label = "REQUEST NODE"
            button.disabled = (
                self._busy
                or self._remote_active
                or self.session.state is not BenchState.SELECTING
            )

    def _remote_status(self) -> dict[str, Any]:
        return {
            "state": self.session.state.value,
            "busy": self._busy,
            "remote_locked": self._remote_active,
            "node": self.session.selected_node,
            "last_rx": self.session.last_serial_message,
            "result": self.session.result,
            "error": self.session.error,
        }

    async def _acquire_remote(self, client_name: str) -> bool:
        if (
            self._busy
            or self.gateway is None
            or self._remote_active
            or isinstance(self.screen, ConfirmUnlinkScreen)
        ):
            return False
        self._remote_active = True
        self._remote_client = client_name
        self._set_busy(False)
        self.query_one("#remote-status", Static).update(
            f"REMOTE · control activo · {client_name}"
        )
        self._refresh_status()
        return True

    async def _release_remote(self, client_name: str) -> None:
        if not self._remote_active or client_name != self._remote_client:
            return
        self._remote_active = False
        self._remote_client = None
        self._set_busy(False)
        port = self.remote_console.bound_port if self.remote_console is not None else getattr(self.args, "remote_port", 8765)
        self.query_one("#remote-status", Static).update(
            f"REMOTE · 127.0.0.1:{port} · SSH"
        )
        self._refresh_status()

    async def _confirm_remote_command(self, command: str) -> bool:
        if command.casefold() != "remote unlink group":
            return True
        loop = asyncio.get_running_loop()
        confirmed: asyncio.Future[bool] = loop.create_future()

        def finish(result: bool | None) -> None:
            if not confirmed.done():
                confirmed.set_result(bool(result))

        self.push_screen(ConfirmUnlinkScreen(), callback=finish)
        try:
            return await asyncio.wait_for(confirmed, timeout=60)
        except TimeoutError:
            return False
        finally:
            if isinstance(self.screen, ConfirmUnlinkScreen):
                self.pop_screen()


def run_tui(args: argparse.Namespace) -> None:
    BenchTuiApp(args).run()
