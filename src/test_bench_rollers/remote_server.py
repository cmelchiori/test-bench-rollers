"""Loopback or Tailscale-bound JSON-lines server for remote UART access."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable
from typing import Any

from .serial_gateway import REQUEST_COMMAND_RE, SerialGateway


StatusProvider = Callable[[], dict[str, Any]]
Acquire = Callable[[str], Awaitable[bool]]
Release = Callable[[str], Awaitable[None]]
Log = Callable[[str], None]
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")


def _is_allowed_bind_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address in TAILSCALE_IPV4


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
        log: Log,
        host: str = "127.0.0.1",
    ) -> None:
        if not _is_allowed_bind_host(host):
            raise ValueError("La consola remota solo puede escuchar en loopback o en una IP IPv4 de Tailscale")
        self.host = host
        self.port = port
        self.gateway_provider = gateway_provider
        self.status_provider = status_provider
        self.acquire = acquire
        self.release = release
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
                await self._send(
                    writer,
                    {"type": "error", "message": "Se esperaba el saludo de la consola"},
                )
                return
            client_name = str(hello.get("client", "sesión remota"))[:80]
            async with self._client_lock:
                if self._clients:
                    await self._send(
                        writer,
                        {
                            "type": "error",
                            "message": "Ya hay una sesión remota conectada",
                        },
                    )
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
                        await self._send(
                            writer,
                            {
                                "type": "control-denied",
                                "message": "El UART ya está bajo control remoto",
                            },
                        )
                    elif owns_control:
                        await self._send(
                            writer,
                            {
                                "type": "control-granted",
                                "message": "Ya tienes el control del UART",
                            },
                        )
                    elif not await self.acquire(client_name):
                        await self._send(
                            writer,
                            {
                                "type": "control-denied",
                                "message": "Banco ocupado; puedes seguir observando y volver a intentar",
                            },
                        )
                    else:
                        owns_control = True
                        self._active_writer = writer
                        self._active_client = client_name
                        self.log(f"Control remoto del UART tomado por {client_name}")
                        await self._send(
                            writer,
                            {
                                "type": "control-granted",
                                "message": "Control UART concedido; las pruebas locales están bloqueadas",
                            },
                        )
                    continue
                if kind == "release":
                    if owns_control:
                        owns_control = False
                        self._active_writer = None
                        self._active_client = None
                        await self.release(client_name)
                        self.log(f"Control remoto del UART liberado por {client_name}")
                    await self._send(
                        writer,
                        {
                            "type": "released",
                            "message": "Control liberado; la sesión continúa en observación",
                        },
                    )
                    continue
                if kind != "command":
                    await self._send(
                        writer,
                        {"type": "error", "message": "Tipo de mensaje no soportado"},
                    )
                    continue
                if not owns_control:
                    await self._send(
                        writer,
                        {
                            "type": "error",
                            "message": "Solo puedes enviar comandos después de tomar el control UART",
                        },
                    )
                    continue
                command = message.get("command")
                if not isinstance(command, str) or not command.strip() or len(command) > 256:
                    await self._send(
                        writer,
                        {"type": "error", "message": "Comando vacío o demasiado largo"},
                    )
                    continue
                if any(ord(character) < 32 or ord(character) == 127 for character in command):
                    await self._send(
                        writer,
                        {
                            "type": "error",
                            "message": "El comando debe ocupar una sola línea",
                        },
                    )
                    continue
                command = command.strip()
                current_gateway = self.gateway_provider()
                if current_gateway is None:
                    await self._send(writer, {"type": "error", "message": "UART desconectado"})
                    continue
                try:
                    request_match = REQUEST_COMMAND_RE.fullmatch(command)
                    cooldown_check = getattr(current_gateway, "request_cooldown_remaining", None)
                    advisory_check = getattr(current_gateway, "request_cooldown_is_advisory", None)
                    cooldown = float(cooldown_check()) if callable(cooldown_check) else 0.0
                    advisory = bool(advisory_check()) if callable(advisory_check) else False
                    if request_match and cooldown > 0 and not advisory:
                        send_forced = getattr(current_gateway, "send_forced_request", None)
                        if callable(send_forced):
                            await send_forced(request_match.group("mesh"))
                        else:
                            await current_gateway.send(command, force=True)
                    else:
                        await current_gateway.send(command)
                except RuntimeError as exc:
                    await self._send(writer, {"type": "error", "message": str(exc)})
                    continue
                self.log(f"TX remoto [{client_name}]: {command}")
                await self._send(writer, {"type": "sent", "command": command})
        except (
            TimeoutError,
            ConnectionError,
            OSError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
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
