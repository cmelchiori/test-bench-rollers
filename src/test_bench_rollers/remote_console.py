"""Textual client for the loopback remote UART console."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from datetime import datetime
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from .remote_server import RemoteConsoleServer
from .tui_views import ConfirmUnlinkScreen

__all__ = ["RemoteConsoleApp", "RemoteConsoleServer", "remote_main"]


class RemoteConsoleApp(App[None]):
    """Touch-friendly remote UART monitor and command console."""

    TITLE = "Roller Test Bench · Remoto"
    SUB_TITLE = "CONSOLA UART DE DIAGNÓSTICO"
    BINDINGS = [("ctrl+c", "quit", "Salir")]
    CSS = """
    Screen { layout: vertical; background: $background; }
    #remote-root { height: 1fr; padding: 0 1; }
    #status-row { height: 4; margin-bottom: 1; }
    #connection-status { width: 1fr; height: 3; padding: 0 1; border: round $primary-darken-1; background: $surface; content-align: left middle; }
    #connection-status.connection-online { color: $success; border: round $success-darken-1; }
    #connection-status.connection-offline { color: $error; border: round $error; }
    #control-status { width: 1fr; height: 3; padding: 0 1; margin-left: 1; border: round $primary-darken-1; background: $surface; content-align: left middle; text-style: bold; }
    #control-status.control-active { color: $error; border: round $error; background: $error 15%; }
    #control-status.control-observe { color: $success; border: round $success-darken-1; }
    #bench-status { height: 2; padding: 0 1; color: $text; border-left: heavy $accent; }
    #control-row { height: 3; margin-bottom: 1; }
    #control-row Button { width: 1fr; height: 3; margin-right: 1; text-style: bold; }
    #quick-row { height: 3; }
    #quick-row Button { width: 1fr; height: 3; margin-right: 1; }
    #log-title { height: 1; content-align: left middle; text-style: bold; color: $accent; }
    #remote-log { height: 1fr; min-height: 3; border: round $primary-darken-1; background: $surface; padding: 0 1; }
    #command-row { height: 3; margin-top: 1; }
    #command { width: 1fr; height: 3; margin-right: 1; }
    #send-command { width: 20; height: 3; text-style: bold; }
    #help { height: 1; color: $text-muted; content-align: left middle; }
    """

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.connected = False
        self.has_control = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="remote-root"):
            with Horizontal(id="status-row"):
                yield Static("Conectando al banco…", id="connection-status")
                yield Static("OBSERVACIÓN", id="control-status", classes="control-observe")
            yield Static("Estado del banco: esperando conexión", id="bench-status")
            with Horizontal(id="control-row"):
                yield Button(
                    "MODO ESCRITURA · TOMAR UART",
                    id="take-control",
                    variant="warning",
                    disabled=True,
                )
                yield Button(
                    "MODO LECTURA · LIBERAR UART",
                    id="release-control",
                    variant="success",
                    disabled=True,
                )
                yield Button("CONECTAR", id="connect", variant="primary")
                yield Button("SALIR", id="quit", variant="error")
            with Horizontal(id="quick-row"):
                yield Button("ESCANEAR GRUPO", id="quick-scan", variant="primary", disabled=True)
                yield Button("UNLINK GROUP", id="quick-unlink", variant="warning", disabled=True)
                yield Button("LIMPIAR REGISTRO", id="clear-log", disabled=True)
            yield Static("TRÁFICO UART · MENSAJES EN TIEMPO REAL", id="log-title")
            yield RichLog(id="remote-log", wrap=True, markup=False, max_lines=2000)
            with Horizontal(id="command-row"):
                yield Input(
                    placeholder="Comando del gateway, por ejemplo: remote scan group",
                    id="command",
                    disabled=True,
                )
                yield Button("ENVIAR", id="send-command", variant="primary", disabled=True)
            yield Static(
                "Lectura: observa el diagnóstico · Escritura: toma el UART y bloquea operaciones locales.",
                id="help",
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._connect(), name="remote-connect", group="remote", exit_on_error=False)

    async def on_unmount(self) -> None:
        if self.writer is not None:
            if not self.writer.is_closing():
                await self._send({"type": "release"})
                self.writer.close()
            try:
                await self.writer.wait_closed()
            except OSError:
                pass

    async def _connect(self) -> None:
        self.query_one("#connection-status", Static).update(f"Conectando a {self.host}:{self.port}…")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            await self._send({"type": "hello", "client": getpass.getuser()})
            self.connected = True
            self.query_one("#connection-status", Static).update(f"Conectado a {self.host}:{self.port}")
            self.query_one("#connection-status", Static).set_class(True, "connection-online")
            self.query_one("#connection-status", Static).set_class(False, "connection-offline")
            self.query_one("#connect", Button).disabled = True
            self.query_one("#take-control", Button).disabled = False
            self.query_one("#bench-status", Static).update("Estado del banco: recibiendo diagnóstico…")
            self._log("Sesión remota conectada en modo solo lectura.")
            await self._receive()
            self.query_one("#connection-status", Static).update("Conexión cerrada por el servicio")
        except (ConnectionError, OSError) as exc:
            self.connected = False
            if self.is_mounted:
                self.query_one("#connection-status", Static).update(f"Sin conexión: {exc}")
                self.query_one("#connection-status", Static).set_class(False, "connection-online")
                self.query_one("#connection-status", Static).set_class(True, "connection-offline")
                self.query_one("#control-status", Static).update("DESCONECTADO")
                self._set_control_style(active=False)
                self.query_one("#connect", Button).disabled = False
                self._log(f"ERROR · No se pudo conectar: {exc}")
        finally:
            self.connected = False
            self.has_control = False
            self.reader = None
            if self.writer is not None:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except OSError:
                    pass
                self.writer = None
            if self.is_mounted:
                self._sync_controls()

    async def _receive(self) -> None:
        assert self.reader is not None
        while raw := await self.reader.readline():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                self._log(raw.decode(errors="replace").rstrip())
                continue
            kind = event.get("type")
            if kind == "connected" or kind == "status":
                self._update_status(event.get("status", {}))
            elif kind == "control-granted":
                self.has_control = True
                self.query_one("#control-status", Static).update("MODO ESCRITURA · UART ACTIVO")
                self._set_control_style(active=True)
                self._log(f"CONTROL · {event.get('message', '')}")
                self._sync_controls()
            elif kind == "control-denied":
                self.has_control = False
                self.query_one("#control-status", Static).update("MODO SOLO LECTURA")
                self._set_control_style(active=False)
                self._log(f"AVISO · {event.get('message', '')}")
                self._sync_controls()
            elif kind == "rx":
                self._log(f"RX  {event.get('line', '')}")
            elif kind == "log":
                self._log(str(event.get("message", "")))
            elif kind == "sent":
                self._log(f"TX  {event.get('command', '')}")
            elif kind == "released":
                self.has_control = False
                self.query_one("#control-status", Static).update("MODO SOLO LECTURA")
                self._set_control_style(active=False)
                self._log(f"CONTROL · {event.get('message', '')}")
                self._sync_controls()
            elif kind == "error":
                self._log(f"ERROR · {event.get('message', '')}")
        self._log("Conexión cerrada por el servicio.")
        self.query_one("#connection-status", Static).update("Conexión cerrada por el servicio")
        self.query_one("#connection-status", Static).set_class(False, "connection-online")
        self.query_one("#connection-status", Static).set_class(True, "connection-offline")

    async def _send(self, message: dict[str, str]) -> None:
        if self.writer is None or self.writer.is_closing():
            return
        self.writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.writer.drain()

    def _update_status(self, status: Any) -> None:
        if not isinstance(status, dict):
            return
        state = status.get("state", "—")
        if isinstance(state, str):
            state = state.replace("_", " ")
        busy = "ocupado" if status.get("busy") else "libre"
        node = status.get("node") or "sin nodo seleccionado"
        result = status.get("result") or "sin resultado"
        self.query_one("#bench-status", Static).update(
            f"Banco: {state} · Operación {busy} · Nodo: {node} · Resultado: {result}"
        )
        if status.get("remote_locked"):
            self.query_one("#control-status", Static).update("MODO ESCRITURA · UART ACTIVO")
            self._set_control_style(active=True)
        elif not self.has_control:
            self.query_one("#control-status", Static).update("MODO SOLO LECTURA")
            self._set_control_style(active=False)
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.query_one("#connect", Button).disabled = self.connected
        self.query_one("#take-control", Button).disabled = not self.connected or self.has_control
        self.query_one("#release-control", Button).disabled = not self.connected or not self.has_control
        self.query_one("#command", Input).disabled = not self.connected or not self.has_control
        self.query_one("#send-command", Button).disabled = not self.connected or not self.has_control
        for button_id in ("#quick-scan", "#quick-unlink"):
            self.query_one(button_id, Button).disabled = not self.connected or not self.has_control
        self.query_one("#clear-log", Button).disabled = not self.connected

    def _log(self, message: str) -> None:
        upper = message.upper()
        style = "bright_cyan" if upper.startswith("RX ") or " RX " in upper else "green"
        if upper.startswith("TX ") or " TX " in upper:
            style = "bright_green"
        if "ERROR" in upper:
            style = "bold red"
        elif "CONTROL" in upper or "AVISO" in upper:
            style = "bold yellow"
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.query_one("#remote-log", RichLog).write(Text(f"{stamp}  {message}", style=style))

    def _set_control_style(self, *, active: bool) -> None:
        status = self.query_one("#control-status", Static)
        status.set_class(active, "control-active")
        status.set_class(not active, "control-observe")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.exit()
        elif event.button.id == "connect" and not self.connected:
            self.run_worker(
                self._connect(),
                name="remote-connect",
                group="remote",
                exit_on_error=False,
            )
        elif event.button.id == "take-control":
            self.run_worker(
                self._send({"type": "acquire"}),
                name="take-control",
                exit_on_error=False,
            )
        elif event.button.id == "release-control":
            self.run_worker(
                self._send({"type": "release"}),
                name="release-control",
                exit_on_error=False,
            )
        elif event.button.id == "send-command":
            self._submit_command()
        elif event.button.id == "quick-scan":
            self._send_command("remote scan group")
        elif event.button.id == "quick-unlink":
            self._request_unlink_confirmation()
        elif event.button.id == "clear-log":
            self.query_one("#remote-log", RichLog).clear()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit_command()

    def _submit_command(self) -> None:
        command = self.query_one("#command", Input)
        value = command.value.strip()
        if not value:
            return
        if value.casefold() == ":take":
            self.run_worker(
                self._send({"type": "acquire"}),
                name="take-control",
                exit_on_error=False,
            )
        elif value.casefold() == ":release":
            self.run_worker(
                self._send({"type": "release"}),
                name="release-control",
                exit_on_error=False,
            )
        elif value.casefold() in {":scan", "remote scan group"}:
            self._send_command("remote scan group")
        elif value.casefold() in {":unlink", "remote unlink group"}:
            self._request_unlink_confirmation()
        else:
            self._send_command(value)
        command.value = ""

    def _request_unlink_confirmation(self) -> None:
        self._log("AVISO · Confirma el unlink para enviarlo a toda la red Mesh.")
        self.push_screen(ConfirmUnlinkScreen(), callback=self._unlink_confirmation_result)

    def _unlink_confirmation_result(self, confirmed: bool | None) -> None:
        if confirmed:
            self._send_command("remote unlink group")
        else:
            self._log("AVISO · Unlink cancelado; no se envió el comando.")

    def _send_command(self, command: str) -> None:
        if not self.has_control:
            self._log("AVISO · Toma el control para cambiar a modo escritura.")
            return
        self.run_worker(
            self._send({"type": "command", "command": command}),
            name="send-command",
            exit_on_error=False,
        )


def remote_main() -> None:
    parser = argparse.ArgumentParser(description="Consola remota del UART del banco")
    parser.add_argument("--host", default="127.0.0.1", help="Host del túnel SSH (por defecto 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RemoteConsoleApp(args.host, args.port).run()
