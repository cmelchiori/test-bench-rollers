"""Interactive serial workflow for the command-line bench interface."""

from __future__ import annotations

import argparse
import asyncio
import re
import time

from .evaluation import _metrics, evaluate
from .serial_gateway import (
    SerialGateway,
    decode_telemetry,
    link_result,
    mac_result,
    normalize_mesh_id,
)
from .state_machine import BenchEvent, BenchSession, BenchState


def show_status(session: BenchSession) -> None:
    print(f"\nEstado: {session.state.value}")
    if session.selected_node:
        print(f"Nodo: {session.selected_node}")
    if session.nodes:
        print(f"Nodos detectados: {', '.join(sorted(session.nodes))}")
    if session.last_serial_message:
        print(f"Último RX: {session.last_serial_message}")
    if session.result:
        print(f"Resultado: {session.result}")
    if session.error:
        print(f"Error: {session.error}")


async def _prompt(text: str) -> str:
    return (await asyncio.to_thread(input, text)).strip()


async def _send_unlink_group(gateway: SerialGateway, listen_seconds: float = 3.0) -> None:
    await gateway.send("remote unlink group")
    print(f"\nTX remote unlink group; procesando RX por {listen_seconds:g} s…")
    received = False
    async for raw in gateway.messages(listen_seconds, progress_label="Unlink group"):
        received = True
        print(f"\n  RX {raw}")
    if not received:
        print("\nSin mensajes RX durante la ventana de escucha.")
    print("Procesamiento de unlink group terminado.")


async def _scan(gateway: SerialGateway, session: BenchSession, seconds: float) -> None:
    session.nodes.clear()
    session.dispatch(BenchEvent.SCAN_STARTED)
    await gateway.send("remote scan group")
    print(f"\nTX remote scan group; escuchando por {seconds:g} s…")
    async for raw in gateway.messages(seconds, progress_label="Búsqueda"):
        session.last_serial_message = raw
        match = re.search(r"<(?P<mesh>0[xX][0-9A-Fa-f]{1,4})>", raw)
        if match:
            mesh_id = normalize_mesh_id(match.group("mesh"))
            if mesh_id not in session.nodes:
                session.nodes.add(mesh_id)
                print(f"\n  Detectado {mesh_id}")
        else:
            print(f"\n  RX {raw}")
    print()
    if session.nodes:
        session.dispatch(BenchEvent.NODES_FOUND)
        show_status(session)
    else:
        print("No se detectaron nodos.")


async def _run_attempt(gateway: SerialGateway, args: argparse.Namespace, session: BenchSession) -> None:
    if session.state is BenchState.IDLE:
        session.dispatch(BenchEvent.START)
        session.dispatch(BenchEvent.READY)
    elif session.state in {BenchState.PASSED, BenchState.FAILED, BenchState.ERROR}:
        session.dispatch(BenchEvent.RESET)

    await _scan(gateway, session, args.scan_seconds)
    if not session.nodes:
        return

    while True:
        requested = await _prompt("Selecciona mesh ID (status / cancelar): ")
        if requested.casefold() in {"cancelar", "cancel"}:
            session.dispatch(BenchEvent.CANCEL)
            return
        if requested.casefold() == "status":
            show_status(session)
            continue
        selected = normalize_mesh_id(requested)
        if selected in session.nodes:
            break
        print("Ese ID no aparece en la lista detectada.")

    session.selected_node = selected
    session.dispatch(BenchEvent.SELECT_NODE)
    identify_timeout = getattr(args, "identify_timeout", 5.0)
    session.mac_address = None
    session.device_id = None
    await gateway.send(f"remote identify {selected}")
    print(f"\nTX remote identify {selected}; consultando MAC por hasta {identify_timeout:g} s…")
    async for raw in gateway.messages(identify_timeout, progress_label="Consulta MAC"):
        session.last_serial_message = raw
        print(f"\n  RX {raw}")
        session.mac_address = mac_result(raw, selected)
        if session.mac_address:
            break
    if session.mac_address:
        print(f"MAC: {session.mac_address}")
    else:
        print(f"No se recibió MAC de {selected}; continúo con la prueba.")

    await gateway.send(f"remote link {selected}")
    session.dispatch(BenchEvent.LINK_SENT)
    print(f"\nTX remote link {selected}; esperando confirmación LINK por hasta {args.link_timeout:g} s…")

    link_confirmed = False
    async for raw in gateway.messages(
        args.link_timeout,
        progress_label="Confirmación del vínculo",
    ):
        session.last_serial_message = raw
        outcome = link_result(raw, selected)
        print(f"\n  RX {raw}")
        if outcome is True:
            link_confirmed = True
            break
        if outcome is False:
            session.dispatch(BenchEvent.LINK_REJECTED, detail=f"El nodo {selected} respondió UNLINK")
            return
    print()
    if not link_confirmed:
        session.dispatch(BenchEvent.ERROR, detail=f"Timeout esperando [LINK] linked de {selected}")
        return

    session.dispatch(BenchEvent.LINK_CONFIRMED)
    if args.link_settle_seconds:
        print(f"Vínculo confirmado; estabilizando {args.link_settle_seconds:g} s…")
        await asyncio.sleep(args.link_settle_seconds)

    remaining = gateway.request_cooldown_remaining()
    advisory = getattr(gateway, "request_cooldown_is_advisory", lambda: False)()
    if remaining > 0 and advisory:
        prompt = f"Tiempo sugerido antes del request: {remaining:.0f} s. ¿Enviar ahora? [s/N]: "
    elif remaining > 0:
        prompt = f"Quedan {remaining:.0f} s de espera. ¿Forzar remote request {selected}? [s/N]: "
    else:
        prompt = f"Enviar remote request {selected}? [s/N]: "
    if (await _prompt(prompt)).casefold() not in {"s", "si", "sí", "y", "yes"}:
        print("Solicitud cancelada por el operador.")
        return

    request_command = f"remote request {selected}"
    if remaining > 0 and not advisory:
        await gateway.send(request_command, force=True)
    else:
        await gateway.send(request_command)
    session.dispatch(BenchEvent.DATA_REQUESTED)
    print(f"\nTX remote request {selected}; esperando telemetría por hasta {args.response_timeout:g} s…")
    started = time.monotonic()
    async for raw in gateway.messages(
        args.response_timeout,
        progress_label="Respuesta de telemetría",
    ):
        session.last_serial_message = raw
        parsed = decode_telemetry(raw)
        if parsed is None:
            print(f"\n  RX {raw}")
            continue
        mesh_id, payload = parsed
        if mesh_id != selected:
            print(f"\n  RX omitido de {mesh_id} (nodo seleccionado {selected})")
            continue
        session.telemetry = payload
        tags = payload.get("tags", {})
        session.device_id = tags.get("device_id") if isinstance(tags, dict) else None
        session.dispatch(BenchEvent.DATA_RECEIVED)
        passed, failures = evaluate(
            payload,
            rpm_min=args.rpm_min,
            rpm_max=args.rpm_max,
            temperature_min=args.temperature_min,
            temperature_max=args.temperature_max,
        )
        session.dispatch(BenchEvent.PASS if passed else BenchEvent.FAIL)
        session.result = "PASS" if passed else "FAIL"
        fields = _metrics(payload)
        elapsed = time.monotonic() - started
        print(f"\nTelemetría {payload.get('schema')} de {mesh_id} en {elapsed:.2f} s")
        print(f"MAC: {session.mac_address or 'No recibida'}")
        print(f"Device-ID: {session.device_id or 'No informado'}")
        print(f"RPM: {fields.get('rpm')}")
        print(f"Temperaturas: {fields.get('temperature')}")
        print(f"Alarmas: {fields.get('alarms')}")
        print(f"Resultado: {session.result}")
        for failure in failures:
            print(f"  - {failure}")
        return
    session.dispatch(BenchEvent.ERROR, detail=f"Timeout esperando telemetría de {selected}")


async def _run_terminal(args: argparse.Namespace) -> None:
    session = BenchSession()
    async with SerialGateway(args.port, args.baudrate) as gateway:
        print(f"Puerto serial abierto: {args.port} @ {args.baudrate} baud")
        while True:
            try:
                await _run_attempt(gateway, args, session)
            except (OSError, ValueError, RuntimeError) as exc:
                if session.state not in {
                    BenchState.ERROR,
                    BenchState.PASSED,
                    BenchState.FAILED,
                }:
                    session.dispatch(BenchEvent.ERROR, detail=str(exc))
            if session.state is BenchState.ERROR:
                show_status(session)

            while True:
                action = await _prompt("Comando: [r] buscar, [u] unlink group, [status], [q] salir: ")
                if action.casefold() in {"q", "quit", "salir"}:
                    return
                if action.casefold() in {"r", "repetir", "buscar"}:
                    break
                if action.casefold() in {"u", "unlink", "unlink group"}:
                    try:
                        await _send_unlink_group(gateway, args.unlink_listen_seconds)
                    except OSError as exc:
                        print(f"No se pudo enviar remote unlink group: {exc}")
                    continue
                if action.casefold() == "status":
                    show_status(session)
                    continue
                print("Comando no reconocido.")
