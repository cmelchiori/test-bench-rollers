"""Visual components and rendering helpers for the bench TUI."""

from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Select, Static, TabbedContent, TabPane

from .evaluation import HARDWARE_ALERT_TEMP_C
from .state_machine import BenchState


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


class BenchTuiViews:
    """Compose and refresh the visual parts of :class:`BenchTuiApp`."""

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
    #detail-panel { width: 1fr; height: 1fr; margin-left: 1; }
    #state { height: auto; min-height: 2; padding: 0 1; color: #8290a2; }
    #home-summary { height: auto; min-height: 1; max-height: 4; padding: 0 1; color: #8290a2; }
    #scan-nodes { height: 1; color: #8290a2; }
    #node-select { width: 1fr; height: 4; margin: 1 0; }
    #clear-test { width: 1fr; height: 3; margin-bottom: 1; text-style: bold; }
    #metrics-columns { height: 1fr; min-height: 12; }
    #metrics, #telemetry-metrics { width: 1fr; height: 1fr; min-height: 12; padding: 1 2; border: round #292d33; background: #111317; color: #dfe5e9; }
    #telemetry-metrics { margin-left: 1; }
    #identity-actions { height: 5; display: none; }
    #identity-actions Button { width: 1fr; height: 4; text-style: bold; }
    #debug-pane { height: 1fr; }
    #debug-description { height: 2; color: #8290a2; }
    #serial-log { height: 1fr; min-height: 5; border: round #292d33; background: #111317; }
    """

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
                            yield Select(
                                [],
                                prompt="Primero busca nodos",
                                allow_blank=True,
                                id="node-select",
                            )
                            yield Button("LIMPIAR PRUEBA", id="clear-test", disabled=True)
                            yield Static("Esperando búsqueda…", id="state")
                        with Vertical(id="detail-panel"):
                            yield Static(
                                "Sin telemetría. Busca un nodo para comenzar.",
                                id="home-summary",
                                markup=False,
                            )
                            with Horizontal(id="metrics-columns"):
                                yield Static(
                                    "MAC: —\nDevice-ID: —\nRPM: —\nTemperaturas: —\nAlarmas: —\nResultado: —",
                                    id="metrics",
                                    markup=False,
                                )
                                yield Static(
                                    "Telemetría: —",
                                    id="telemetry-metrics",
                                    markup=False,
                                )
                    with Horizontal(id="identity-actions"):
                        yield Button(
                            "REINTENTAR IDENTIDAD",
                            id="retry-identity",
                            variant="warning",
                        )
            with TabPane("Depuración", id="debug-tab"):
                with Vertical(id="debug-pane"):
                    yield Static(
                        "Registro serial de solo consulta para diagnóstico.",
                        id="debug-description",
                    )
                    yield RichLog(id="serial-log", wrap=True, markup=False, max_lines=1000)

    def _refresh_scan_nodes(self) -> None:
        nodes = sorted(self.session.nodes)
        display = f"Nodos detectados: {', '.join(nodes)}" if nodes else "Nodos: ninguno"
        self._update_visible("scan-nodes", display)

    def _render_home_summary(self) -> None:
        if self.session.state is BenchState.NO_NODES:
            summary = "No se detectaron nodos. Puedes iniciar otra búsqueda cuando quieras."
        elif self.session.result:
            summary = "Prueba completada. El resultado y sus detalles aparecen abajo."
        elif self.session.nodes:
            summary = f"{len(self.session.nodes)} nodo(s) detectado(s). Selecciona uno y solicita la prueba."
        else:
            summary = "Sin telemetría. Busca un nodo para comenzar."
        self._update_visible("home-summary", summary)

    def _render_metrics(self, failures: list[str] | None = None) -> None:
        selected = self.session.selected_node or "Sin nodo seleccionado"
        identity = Text()
        telemetry = Text()

        def add_line(
            content: Text,
            label: str = "",
            value: str = "",
            *,
            value_style: str | None = None,
        ) -> None:
            if content.plain:
                content.append("\n")
            if label:
                content.append(label, style="bold")
            if value:
                content.append(value, style=value_style)

        add_line(identity, "Nodo seleccionado: ", selected)
        add_line(identity, "Device ID: ", self.session.device_id or "—")
        add_line(identity, "MAC: ", self.session.mac_address or "No recibida")
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
                if self.session.state in {BenchState.PASSED, BenchState.FAILED, BenchState.IDENTITY_PENDING}
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
            add_line(identity, "Resultado QA: ", result, value_style=result_style)
            add_line(identity, "Criterios aplicados:")
            add_line(identity, value=f"  - RPM > 0{rpm_range}")
            add_line(
                identity,
                value=f"  - Temperaturas válidas y ≤ {HARDWARE_ALERT_TEMP_C} °C{temperature_range}",
            )
            add_line(identity, value="  - Sin alarmas latched activas")
            add_line(telemetry, "Telemetría")
            add_line(telemetry, "RPM: ", f"{fields.get('rpm', '—')} RPM")
            add_line(telemetry, "Respuesta: ", "recibida")
            add_line(telemetry, "Conexión: ", "Online" if self.gateway else "Offline")
            add_line(telemetry, "Link: ", link_state)
            add_line(telemetry, "Temperaturas")
            for temperature in temperature_lines or ["Sin lecturas de temperatura"]:
                add_line(telemetry, value=temperature)
            add_line(telemetry, "Alarmas: ", str(fields.get("alarms", "—")))
            if failures:
                add_line(telemetry, "Fallas:")
                for failure in failures:
                    add_line(telemetry, value=f"  - {failure}")
        self.query_one("#metrics", Static).update(identity)
        self.query_one("#telemetry-metrics", Static).update(telemetry if payload else "Sin telemetría recibida.")

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
                asyncio.create_task(self.remote_console.publish({"type": "status", "status": remote_status}))

    def _request_wait_seconds(self) -> float:
        if self.gateway is None:
            return 0.0
        cooldown = getattr(self.gateway, "request_cooldown_remaining", None)
        return max(0.0, float(cooldown())) if callable(cooldown) else 0.0

    def _can_request_selected(self) -> bool:
        if self.session.state is BenchState.SELECTING:
            return True
        return self.session.link_confirmed and self.session.state in {
            BenchState.PASSED,
            BenchState.FAILED,
            BenchState.ERROR,
        }

    def _header_status_text(self) -> str:
        if self._remote_active:
            return "UART bajo control remoto"
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
            advisory = getattr(self.gateway, "request_cooldown_is_advisory", None) if self.gateway else None
            button.label = (
                f"REQUEST NODE · {seconds}s" if callable(advisory) and advisory() else f"REQUEST FORZADO {seconds}s"
            )
        else:
            button.label = "REQUEST NODE"
        button.disabled = self._busy or self._remote_active or not self._can_request_selected()
