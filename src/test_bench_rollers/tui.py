"""Textual terminal interface for operating the roller bench."""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from typing import Any

from textual.app import App
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Button, RichLog, Select, Static

from .evaluation import evaluate
from .remote_server import RemoteConsoleServer
from .serial_gateway import (
    SerialGateway,
    decode_telemetry,
    link_result,
    mac_result,
    normalize_mesh_id,
)
from .state_machine import BenchEvent, BenchSession, BenchState
from .tui_views import BenchTuiViews, ConfirmUnlinkScreen


class BenchTuiApp(BenchTuiViews, App[None]):
    """Interactive TUI; serial operations run as Textual workers."""

    TITLE = "Roller Test Bench"
    SUB_TITLE = "CONTROL DE NODOS MESH · BANCO DE RODILLOS"
    BINDINGS = [
        ("s", "scan", "Buscar"),
        ("t", "test_node", "Probar nodo"),
        ("u", "unlink_group", "Unlink grupo"),
        ("q", "quit_app", "Salir"),
    ]

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
        self.remote_console: RemoteConsoleServer | None = None
        self._last_remote_status: dict[str, Any] | None = None

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
            self.query_one("#connection", Static).update(f"UART · {self.args.port} @ {self.args.baudrate} · conectado")
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
        actions = {
            "scan": (self._scan, "scan"),
            "test": (self._test_selected, "test"),
            "retry-identity": (self._retry_identity, "retry-identity"),
            "unlink": (self._unlink_group, "unlink"),
        }
        if event.button.id == "clear-test":
            self._clear_test()
            return
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

    def _unlink_confirmation_result(self, confirmed: bool | None) -> None:
        if confirmed:
            self._start_action(self._unlink_group(), "unlink")
        else:
            self._log("Unlink group cancelado por el operador")

    def _clear_test(self) -> None:
        if self._busy or self._remote_active:
            return
        try:
            self.session.dispatch(BenchEvent.CLEAR_TEST)
        except ValueError as exc:
            self._set_error(str(exc))
            return
        self.query_one("#identity-actions", Horizontal).display = False
        selector = self.query_one("#node-select", Select)
        selector.clear()
        self._update_visible("home-summary", "Prueba limpiada. Selecciona un nodo para iniciar otra.")
        self._render_metrics()
        self._log("Resultado y datos de prueba limpiados; nodos detectados conservados")
        self._set_busy(False)
        self._refresh_status()

    def action_scan(self) -> None:
        self._start_action(self._scan(), "scan")

    def action_test_node(self) -> None:
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
                    self._log(f"Un solo nodo detectado ({selector.value}); listo para solicitud manual")
            else:
                self._log("Búsqueda terminada: no se detectaron nodos")
            self._refresh_scan_nodes()
            self._render_home_summary()
        except (OSError, ValueError, RuntimeError) as exc:
            self._set_error(str(exc))
        finally:
            self._end_activity()
            self._set_busy(False)

    async def _test_selected(self) -> None:
        try:
            if self.gateway is None:
                raise RuntimeError("El puerto serial no está conectado")
            if self.session.link_confirmed and self.session.state in {
                BenchState.PASSED,
                BenchState.FAILED,
                BenchState.ERROR,
            }:
                await self._request_selected_again()
                return
            selected = self.query_one("#node-select", Select).value
            if not isinstance(selected, str) or selected not in self.session.nodes:
                raise ValueError("Selecciona un nodo detectado")
            if self.session.state is not BenchState.SELECTING:
                raise ValueError(f"No hay un nodo listo para probar ({self.session.state.value})")
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
                    self.session.dispatch(
                        BenchEvent.LINK_REJECTED,
                        detail=f"El nodo {selected} respondió UNLINK",
                    )
                    self._refresh_status()
                    return
            if not linked:
                self._set_error(f"Timeout esperando confirmación LINK de {selected}")
                return

            self.session.dispatch(BenchEvent.LINK_CONFIRMED)
            if self.args.link_settle_seconds:
                self._begin_activity("Estabilizando vínculo", self.args.link_settle_seconds)
                await asyncio.sleep(self.args.link_settle_seconds)

            await self._send_manual_request(selected)
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
            self._set_busy(False)

    async def _request_selected_again(self) -> None:
        selected = self.session.selected_node
        if self.gateway is None or selected is None or not self.session.link_confirmed:
            raise ValueError("No hay un nodo vinculado listo para solicitar telemetría")
        await self._send_manual_request(selected)
        self.session.dispatch(BenchEvent.DATA_REQUESTED)
        self.session.telemetry = None
        self._render_metrics()
        self._log(f"TX remote request {selected} (reintento manual)")
        self._begin_activity("Esperando telemetría", self.args.response_timeout)
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
                self._log("Request recibido, pero falta MAC o device-id")
                return
            self._apply_quality_result()
            self._log(f"Prueba {self.session.result}: {selected}")
            return
        self._set_error(f"Timeout esperando telemetría de {selected}")

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
                await self._request_identity_telemetry(selected, getattr(self.args, "response_timeout", 10.0))
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
        await self._send_manual_request(mesh_id)
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

    async def _send_manual_request(self, mesh_id: str) -> None:
        assert self.gateway is not None
        remaining = self._request_wait_seconds()
        advisory = getattr(self.gateway, "request_cooldown_is_advisory", None)
        if remaining > 0 and callable(advisory) and advisory():
            await self.gateway.send(f"remote request {mesh_id}")
            return
        send_forced = getattr(self.gateway, "send_forced_request", None)
        if callable(send_forced):
            await send_forced(mesh_id)
        else:
            await self.gateway.send(f"remote request {mesh_id}")

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
        if self.session.state not in {
            BenchState.ERROR,
            BenchState.PASSED,
            BenchState.FAILED,
        }:
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
        clear_button = self.query_one("#clear-test", Button)
        clear_button.disabled = (
            busy
            or self._remote_active
            or not (self.session.selected_node or self.session.telemetry or self.session.result or self.session.error)
        )
        self.query_one("#test", Button).disabled = busy or self._remote_active or not self._can_request_selected()
        self._refresh_request_button()
        self.query_one("#identity-actions", Horizontal).display = self.session.state is BenchState.IDENTITY_PENDING

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
        if self._busy or self.gateway is None or self._remote_active or isinstance(self.screen, ConfirmUnlinkScreen):
            return False
        self._remote_active = True
        self._remote_client = client_name
        self._set_busy(False)
        self.query_one("#remote-status", Static).update(f"REMOTE · control activo · {client_name}")
        self._refresh_status()
        return True

    async def _release_remote(self, client_name: str) -> None:
        if not self._remote_active or client_name != self._remote_client:
            return
        self._remote_active = False
        self._remote_client = None
        self._set_busy(False)
        port = (
            self.remote_console.bound_port
            if self.remote_console is not None
            else getattr(self.args, "remote_port", 8765)
        )
        self.query_one("#remote-status", Static).update(f"REMOTE · 127.0.0.1:{port} · SSH")
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
