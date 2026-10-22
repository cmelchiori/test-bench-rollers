"""Loopback JSON-lines UART console for use through an authenticated SSH tunnel."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from datetime import datetime
from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static
from rich.text import Text

from .serial_gateway import SerialGateway

StatusProvider = Callable[[], dict[str, Any]]
Acquire = Callable[[str], Awaitable[bool]]
Release = Callable[[str], Awaitable[None]]
CommandConfirm = Callable[[str], Awaitable[bool]]
Log = Callable[[str], None]


class RemoteConsoleServer:
    """Multiplex UART access from one remote console without opening the tty twice."""

    def __init__(
        self,
        *,
        port: int,
        gateway_provider: Callable[[], SerialGateway | None],
        status_provider: StatusProvider,
        acquire: Acquire,
        release: Release,
        confirm_command: CommandConfirm,
        log: Log,
        host: str = "127.0.0.1",
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("La consola remota solo puede escuchar en loopback; usa SSH para acceso remoto")
        self.host = host
        self.port = port
        self.gateway_provider = gateway_provider
        self.status_provider = status_provider
        self.acquire = acquire
        self.release = release
        self.confirm_command = confirm_command
        self.log = log
        self.server: asyncio.AbstractServer | None = None
        self._active_writer: asyncio.StreamWriter | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._send_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._active_client: str | None = None

    @property
    def active_client(self) -> str | None:
        return self._active_client

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port, limit=4096)

    @property
    def bound_port(self) -> int:
        if self.server is None or not self.server.sockets:
            return self.port
        return int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self._active_client is not None:
            client = self._active_client
            self._active_writer = None
            self._active_client = None
            await self.release(client)
        for writer in tuple(self._clients):
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self._clients.clear()

    async def publish(self, event: dict[str, Any]) -> None:
        if not self._clients:
            return
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._send_lock:
            for writer in tuple(self._clients):
                if writer.is_closing():
                    self._clients.discard(writer)
                    continue
                writer.write(encoded.encode("utf-8"))
                try:
                    await writer.drain()
                except (ConnectionError, OSError):
                    self._clients.discard(writer)

    async def _send(self, writer: asyncio.StreamWriter, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._send_lock:
            writer.write(encoded.encode("utf-8"))
            await writer.drain()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        subscriber: asyncio.Queue[str] | None = None
        rx_task: asyncio.Task[None] | None = None
        client_name = "sesión remota"
        owns_control = False
        line_task: asyncio.Task[bytes] | None = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            hello = json.loads(raw)
            if hello.get("type") != "hello":
                await self._send(writer, {"type": "error", "message": "Se esperaba el saludo de la consola"})
                return
            client_name = str(hello.get("client", "sesión remota"))[:80]
            async with self._client_lock:
                if self._clients:
                    await self._send(writer, {"type": "error", "message": "Ya hay una sesión remota conectada"})
                    return
                self._clients.add(writer)
            self.log(f"Observador remoto conectado: {client_name} desde {peer}")
            await self._send(writer, {"type": "connected", "status": self.status_provider()})
            gateway = self.gateway_provider()
            if gateway is None:
                raise RuntimeError("UART desconectado")
            subscriber = gateway.subscribe()
            rx_task = asyncio.create_task(self._pump_rx(subscriber), name="remote-uart-rx")
            line_task = asyncio.create_task(reader.readline())
            pending_raw: bytes | None = None
            while True:
                raw = pending_raw if pending_raw is not None else await line_task
                pending_raw = None
                if not raw:
                    break
                line_task = asyncio.create_task(reader.readline())
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await self._send(writer, {"type": "error", "message": "Mensaje JSON inválido"})
                    continue
                kind = message.get("type")
                if kind == "acquire":
                    if self._active_writer is not None and self._active_writer is not writer:
                        await self._send(writer, {"type": "control-denied", "message": "El UART ya está bajo control remoto"})
                    elif owns_control:
                        await self._send(writer, {"type": "control-granted", "message": "Ya tienes el control del UART"})
                    elif not await self.acquire(client_name):
                        await self._send(writer, {"type": "control-denied", "message": "Banco ocupado; puedes seguir observando y volver a intentar"})
                    else:
                        owns_control = True
                        self._active_writer = writer
                        self._active_client = client_name
                        self.log(f"Control remoto del UART tomado por {client_name}")
                        await self._send(writer, {"type": "control-granted", "message": "Control UART concedido; las pruebas locales están bloqueadas"})
                    continue
                if kind == "release":
                    if owns_control:
                        owns_control = False
                        self._active_writer = None
                        self._active_client = None
                        await self.release(client_name)
                        self.log(f"Control remoto del UART liberado por {client_name}")
                    await self._send(writer, {"type": "released", "message": "Control liberado; la sesión continúa en observación"})
                    continue
                if kind != "command":
                    await self._send(writer, {"type": "error", "message": "Tipo de mensaje no soportado"})
                    continue
                if not owns_control:
                    await self._send(writer, {"type": "error", "message": "Solo puedes enviar comandos después de tomar el control UART"})
                    continue
                command = message.get("command")
                if not isinstance(command, str) or not command.strip() or len(command) > 256:
                    await self._send(writer, {"type": "error", "message": "Comando vacío o demasiado largo"})
                    continue
                if any(ord(character) < 32 or ord(character) == 127 for character in command):
                    await self._send(writer, {"type": "error", "message": "El comando debe ocupar una sola línea"})
                    continue
                command = command.strip()
                if command.casefold() == "remote unlink group":
                    confirmation = asyncio.create_task(self.confirm_command(command))
                    done, _ = await asyncio.wait(
                        {confirmation, line_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if line_task in done:
                        pending_raw = line_task.result()
                        line_task = asyncio.create_task(reader.readline())
                        confirmation.cancel()
                        try:
                            await confirmation
                        except asyncio.CancelledError:
                            pass
                        if not pending_raw:
                            break
                        await self._send(writer, {
                            "type": "error",
                            "message": "Unlink Group cancelado; llegó otro mensaje antes de la confirmación local",
                        })
                        continue
                    if not confirmation.result():
                        await self._send(writer, {"type": "error", "message": "Unlink Group cancelado por el operador"})
                        continue
                current_gateway = self.gateway_provider()
                if current_gateway is None:
                    await self._send(writer, {"type": "error", "message": "UART desconectado"})
                    continue
                try:
                    await current_gateway.send(command)
                except RuntimeError as exc:
                    await self._send(writer, {"type": "error", "message": str(exc)})
                    continue
                self.log(f"TX remoto [{client_name}]: {command}")
                await self._send(writer, {"type": "sent", "command": command})
        except (asyncio.TimeoutError, ConnectionError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            if not writer.is_closing():
                try:
                    await self._send(writer, {"type": "error", "message": str(exc)})
                except (ConnectionError, OSError):
                    pass
        finally:
            if line_task is not None and not line_task.done():
                line_task.cancel()
                try:
                    await line_task
                except asyncio.CancelledError:
                    pass
            if rx_task is not None:
                rx_task.cancel()
                try:
                    await rx_task
                except asyncio.CancelledError:
                    pass
            gateway = self.gateway_provider()
            if subscriber is not None and gateway is not None:
                gateway.unsubscribe(subscriber)
            if owns_control:
                if self._active_writer is writer:
                    self._active_writer = None
                    self._active_client = None
                await self.release(client_name)
                self.log(f"Control remoto liberado al desconectar: {client_name}")
            self.log(f"Observador remoto desconectado: {client_name}")
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _pump_rx(self, subscriber: asyncio.Queue[str]) -> None:
        while True:
            line = await subscriber.get()
            await self.publish({"type": "rx", "line": line})


class RemoteConsoleApp(App[None]):
    """Touch-friendly remote UART monitor and command console."""

    TITLE = "Roller Test Bench · Remoto"
    SUB_TITLE = "CONSOLA UART DE DIAGNÓSTICO"
    BINDINGS = [("ctrl+c", "quit", "Salir")]
    CSS = """
    Screen { layout: vertical; background: $background; }
    #remote-root { height: 1fr; padding: 1 2; }
    #status-row { height: 5; margin-bottom: 1; }
    #connection-status { width: 1fr; height: 4; padding: 1 2; border: round $primary-darken-1; background: $surface; content-align: left middle; }
    #control-status { width: 1fr; height: 4; padding: 1 2; margin-left: 1; border: round $primary-darken-1; background: $surface; content-align: left middle; text-style: bold; }
    #control-status.control-active { color: $error; border: round $error; background: $error 15%; }
    #control-status.control-observe { color: $success; border: round $success-darken-1; }
    #bench-status { height: 3; padding: 0 1; color: $text-muted; }
    #control-row { height: 5; margin-bottom: 1; }
    #control-row Button { width: 1fr; height: 4; margin-right: 1; text-style: bold; }
    #log-title { height: 2; content-align: left middle; text-style: bold; color: $accent; }
    #remote-log { height: 1fr; min-height: 8; border: round $primary-darken-1; background: $surface; padding: 0 1; }
    #command-row { height: 5; margin-top: 1; }
    #command { width: 1fr; height: 4; margin-right: 1; }
    #send-command { width: 20; height: 4; text-style: bold; }
    #help { height: 2; color: $text-muted; content-align: left middle; }
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
                yield Button("TOMAR CONTROL UART", id="take-control", variant="warning", disabled=True)
                yield Button("LIBERAR UART", id="release-control", variant="success", disabled=True)
                yield Button("CONECTAR", id="connect", variant="primary")
                yield Button("SALIR", id="quit", variant="error")
            yield Static("TRÁFICO UART · MENSAJES EN TIEMPO REAL", id="log-title")
            yield RichLog(id="remote-log", wrap=True, markup=False, max_lines=2000)
            with Horizontal(id="command-row"):
                yield Input(placeholder="Comando del gateway, por ejemplo: remote scan group", id="command", disabled=True)
                yield Button("ENVIAR", id="send-command", variant="primary", disabled=True)
            yield Static("Al tomar el control se bloquean las pruebas locales. :take / :release también están disponibles.", id="help")
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
            await self._send({"type": "acquire"})
            self.connected = True
            self.query_one("#connection-status", Static).update(f"Conectado a {self.host}:{self.port}")
            self.query_one("#connect", Button).disabled = True
            self.query_one("#take-control", Button).disabled = False
            self.query_one("#bench-status", Static).update("Estado del banco: recibiendo diagnóstico…")
            self._log("Sesión remota conectada; se solicitó control del UART.")
            await self._receive()
            self.query_one("#connection-status", Static).update("Conexión cerrada por el servicio")
        except (ConnectionError, OSError) as exc:
            self.connected = False
            if self.is_mounted:
                self.query_one("#connection-status", Static).update(f"Sin conexión: {exc}")
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
            if kind == "connected":
                self._update_status(event.get("status", {}))
            elif kind == "status":
                self._update_status(event.get("status", {}))
            elif kind == "control-granted":
                self.has_control = True
                self.query_one("#control-status", Static).update("CONTROL UART ACTIVO · PRUEBAS BLOQUEADAS")
                self._set_control_style(active=True)
                self._log(f"CONTROL · {event.get('message', '')}")
                self._sync_controls()
            elif kind == "control-denied":
                self.has_control = False
                self.query_one("#control-status", Static).update("OBSERVACIÓN · CONTROL NO DISPONIBLE")
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
                self.query_one("#control-status", Static).update("OBSERVACIÓN · UART LIBRE")
                self._set_control_style(active=False)
                self._log(f"CONTROL · {event.get('message', '')}")
                self._sync_controls()
            elif kind == "error":
                self._log(f"ERROR · {event.get('message', '')}")
        self._log("Conexión cerrada por el servicio.")

    async def _send(self, message: dict[str, str]) -> None:
        if self.writer is None or self.writer.is_closing():
            return
        self.writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.writer.drain()

    def _update_status(self, status: Any) -> None:
        if not isinstance(status, dict):
            return
        state = status.get("state", "—")
        busy = "ocupado" if status.get("busy") else "libre"
        node = status.get("node") or "sin nodo seleccionado"
        result = status.get("result") or "sin resultado"
        self.query_one("#bench-status", Static).update(
            f"Banco: {state} · Operación {busy} · Nodo: {node} · Resultado: {result}"
        )
        if status.get("remote_locked"):
            self.query_one("#control-status", Static).update("CONTROL UART ACTIVO · PRUEBAS BLOQUEADAS")
            self._set_control_style(active=True)

    def _sync_controls(self) -> None:
        self.query_one("#connect", Button).disabled = self.connected
        self.query_one("#take-control", Button).disabled = not self.connected or self.has_control
        self.query_one("#release-control", Button).disabled = not self.connected or not self.has_control
        self.query_one("#command", Input).disabled = not self.connected or not self.has_control
        self.query_one("#send-command", Button).disabled = not self.connected or not self.has_control

    def _log(self, message: str) -> None:
        upper = message.upper()
        style = "bright_cyan" if upper.startswith("RX ") or " RX " in upper else "green"
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
            self.run_worker(self._connect(), name="remote-connect", group="remote", exit_on_error=False)
        elif event.button.id == "take-control":
            self.run_worker(self._send({"type": "acquire"}), name="take-control", exit_on_error=False)
        elif event.button.id == "release-control":
            self.run_worker(self._send({"type": "release"}), name="release-control", exit_on_error=False)
        elif event.button.id == "send-command":
            self._submit_command()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit_command()

    def _submit_command(self) -> None:
        command = self.query_one("#command", Input)
        value = command.value.strip()
        if not value:
            return
        if value.casefold() == ":take":
            self.run_worker(self._send({"type": "acquire"}), name="take-control", exit_on_error=False)
        elif value.casefold() == ":release":
            self.run_worker(self._send({"type": "release"}), name="release-control", exit_on_error=False)
        else:
            if value.casefold() == "remote unlink group":
                self._log("AVISO · Esperando confirmación en la pantalla local…")
            self.run_worker(
                self._send({"type": "command", "command": value}),
                name="send-command",
                exit_on_error=False,
            )
        command.value = ""


def remote_main() -> None:
    parser = argparse.ArgumentParser(description="Consola remota del UART del banco")
    parser.add_argument("--host", default="127.0.0.1", help="Host del túnel SSH (por defecto 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RemoteConsoleApp(args.host, args.port).run()
