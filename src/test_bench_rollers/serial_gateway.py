"""Small UART adapter that follows the gateway command and message conventions."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncIterator

from aioserial import AioSerial
from mesh_schemas import (
    ROLLER_DATA_V10_SCHEMA,
    ROLLER_DATA_V11_SCHEMA,
    ROLLER_DATA_V12_SCHEMA,
    decode_base85_payload,
)

SERIAL_DATA_RE = re.compile(
    r"<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>\s*(?:\[(?P<type>DATA[A-Za-z0-9.]*)\]:?\s*)?(?P<payload>.+)"
)
MAC_MESSAGE_RE = re.compile(
    r"^<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>\s*[\[<]MAC[\]>]:?\s*(?P<text>.*)$",
    re.IGNORECASE,
)
MAC_ADDRESS_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
ANSI_ESCAPE_RE = re.compile(r"(?:\x1B[@-_][0-?]*[ -/]*[@-~])")
SCHEMA_BY_MESSAGE = {
    "DATA": ROLLER_DATA_V10_SCHEMA,
    "DATA1.0": ROLLER_DATA_V10_SCHEMA,
    "DATA1.1": ROLLER_DATA_V11_SCHEMA,
    "DATAX": ROLLER_DATA_V12_SCHEMA,
    "DATA1.2": ROLLER_DATA_V12_SCHEMA,
}
REQUEST_COOLDOWN_SECONDS = 65.0
REQUEST_COMMAND_RE = re.compile(r"^remote\s+request\s+(?P<mesh>0[xX][0-9A-Fa-f]{1,4})$", re.IGNORECASE)
IS_REQUEST_COMMAND_RE = re.compile(r"^remote\s+request(?:\s|$)", re.IGNORECASE)


def normalize_mesh_id(value: str) -> str:
    raw = value.strip()
    if raw.lower().startswith("0x"):
        raw = raw[2:]
    return f"0x{raw.upper().zfill(4)}"


def decode_telemetry(raw: str) -> tuple[str, dict[str, object]] | None:
    """Parse one gateway-style BLE line and decode its explicitly selected schema."""
    match = SERIAL_DATA_RE.fullmatch(raw.strip())
    if match is None:
        return None
    kind = (match.group("type") or "").upper()
    if not kind:
        kind = {120: "DATA", 160: "DATAX"}.get(len(match.group("payload")), "")
    schema = SCHEMA_BY_MESSAGE.get(kind)
    if schema is None:
        return None
    decoded = decode_base85_payload(match.group("payload").strip(), schema=schema)
    if decoded is None:
        return None
    return normalize_mesh_id(match.group("mesh")), decoded.model_dump(mode="json", by_alias=True)


def link_result(raw: str, mesh_id: str) -> bool | None:
    """Return True/False for an explicit gateway LINK/UNLINK response."""
    match = re.match(
        r"^<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>\s*\[(?P<kind>LINK|UNLINK)\]\s*(?P<text>.*)$",
        raw.strip(),
        re.IGNORECASE,
    )
    if match is None or normalize_mesh_id(match.group("mesh")) != normalize_mesh_id(mesh_id):
        return None
    link_status = " ".join(match.group("text").casefold().strip().rstrip(".! ").split())
    if match.group("kind").upper() == "LINK" and link_status in {"linked", "linked ok"}:
        return True
    if match.group("kind").upper() == "UNLINK":
        return False
    return None


def mac_result(raw: str, mesh_id: str) -> str | None:
    """Return the MAC from a gateway [MAC] response for the requested node."""
    match = MAC_MESSAGE_RE.match(raw.strip())
    if match is None or normalize_mesh_id(match.group("mesh")) != normalize_mesh_id(mesh_id):
        return None
    address = MAC_ADDRESS_RE.search(match.group("text"))
    return address.group(0).upper() if address else None


class SerialGateway:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.2) -> None:
        self.serial = AioSerial(port=port, baudrate=baudrate, timeout=timeout)
        self.rx: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._reader: asyncio.Task[None] | None = None
        self._tx_lock = asyncio.Lock()
        self._request_cooldown_started_at: float | None = None
        self._request_timer_is_advisory = False
        self._pending_request_mesh: str | None = None

    def request_cooldown_remaining(self) -> float:
        if self._request_cooldown_started_at is None:
            return 0.0
        return max(
            0.0,
            self._request_cooldown_started_at + REQUEST_COOLDOWN_SECONDS - time.monotonic(),
        )

    def request_cooldown_is_advisory(self) -> bool:
        return self._request_timer_is_advisory

    def _complete_pending_request(self) -> None:
        self._request_cooldown_started_at = time.monotonic()
        self._request_timer_is_advisory = False
        self._pending_request_mesh = None

    async def __aenter__(self) -> SerialGateway:
        self._reader = asyncio.create_task(self._read_loop(), name="bench-serial-reader")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        self.serial.close()

    async def _read_loop(self) -> None:
        while True:
            try:
                line = (await self.serial.readline_async()).decode("utf-8", errors="ignore").strip()
                line = ANSI_ESCAPE_RE.sub("", line).replace("uart:~$", "").strip()
                if line:
                    await self.rx.put(line)
                    parsed = decode_telemetry(line)
                    data_match = SERIAL_DATA_RE.fullmatch(line)
                    response_mesh = (
                        normalize_mesh_id(data_match.group("mesh"))
                        if data_match is not None
                        else parsed[0]
                        if parsed is not None
                        else None
                    )
                    if (
                        response_mesh is not None
                        and self._pending_request_mesh is not None
                        and self._pending_request_mesh in {"*", response_mesh}
                    ):
                        self._complete_pending_request()
                    for subscriber in tuple(self._subscribers):
                        if subscriber.full():
                            with contextlib.suppress(asyncio.QueueEmpty):
                                subscriber.get_nowait()
                        subscriber.put_nowait(line)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.rx.put(f"__SERIAL_ERROR__ {type(exc).__name__}: {exc}")
                return

    async def send(self, command: str, *, force: bool = False) -> None:
        async with self._tx_lock:
            request_match = REQUEST_COMMAND_RE.fullmatch(command.strip())
            is_request = IS_REQUEST_COMMAND_RE.match(command.strip()) is not None
            if is_request:
                wait_seconds = self.request_cooldown_remaining()
                if wait_seconds > 0 and not force and not self._request_timer_is_advisory:
                    raise RuntimeError(
                        f"remote request disponible en {wait_seconds:.0f} s "
                        f"(mínimo {REQUEST_COOLDOWN_SECONDS:.0f} s desde la última respuesta o timeout)"
                    )
            await self.serial.write_async(command.encode("utf-8") + b"\r")
            flush = getattr(self.serial, "flush", None)
            if callable(flush):
                flush_task = asyncio.create_task(asyncio.to_thread(flush))
                try:
                    await asyncio.shield(flush_task)
                except asyncio.CancelledError:
                    await asyncio.shield(flush_task)
                    raise
            if command.strip().casefold() == "remote scan group" and self._request_cooldown_started_at is None:
                self._request_cooldown_started_at = time.monotonic()
                self._request_timer_is_advisory = True
            if is_request:
                self._pending_request_mesh = (
                    normalize_mesh_id(request_match.group("mesh")) if request_match is not None else "*"
                )

    async def send_forced_request(self, mesh_id: str) -> None:
        await self.send(f"remote request {mesh_id}", force=True)

    def subscribe(self, *, max_queue_size: int = 500) -> asyncio.Queue[str]:
        """Subscribe to new RX lines without stealing them from workflow consumers."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    async def messages(self, timeout: float, *, progress_label: str | None = None) -> AsyncIterator[str]:
        """Yield received lines until timeout, showing elapsed/remaining time."""
        started = time.monotonic()
        deadline = asyncio.get_running_loop().time() + timeout
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                line = await asyncio.wait_for(self.rx.get(), timeout=min(remaining, 1.0))
            except TimeoutError:
                if progress_label:
                    elapsed = min(timeout, time.monotonic() - started)
                    left = max(0.0, timeout - elapsed)
                    print(
                        f"\r{progress_label}: {elapsed:.0f} s transcurridos, {left:.0f} s restantes",
                        end="",
                        flush=True,
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    break
                continue
            if line.startswith("__SERIAL_ERROR__"):
                raise OSError(line.removeprefix("__SERIAL_ERROR__ "))
            if progress_label:
                elapsed = min(timeout, time.monotonic() - started)
                left = max(0.0, timeout - elapsed)
                print(
                    f"\r{progress_label}: {elapsed:.0f} s transcurridos, {left:.0f} s restantes",
                    end="",
                    flush=True,
                )
            yield line
        if progress_label:
            print()
        if self._pending_request_mesh is not None:
            self._complete_pending_request()
