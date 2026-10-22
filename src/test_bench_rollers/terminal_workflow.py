"""Interactive serial workflow for the command-line bench interface."""

from __future__ import annotations

import argparse
import asyncio

from .bench_workflow import BenchWorkflow
from .evaluation import _metrics
from .serial_gateway import SerialGateway, normalize_mesh_id
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
    workflow = BenchWorkflow(
        gateway,
        session,
        on_status=lambda message: print(f"\n{message}"),
        on_message=lambda raw: print(f"\n  RX {raw}"),
    )
    await workflow.scan(seconds)
    if session.nodes:
        show_status(session)


async def _approve_request(remaining: float, advisory: bool, mesh_id: str) -> bool:
    if remaining > 0 and advisory:
        prompt = f"Tiempo sugerido antes del request: {remaining:.0f} s. ¿Enviar ahora? [s/N]: "
    elif remaining > 0:
        prompt = f"Quedan {remaining:.0f} s de espera. ¿Forzar remote request {mesh_id}? [s/N]: "
    else:
        prompt = f"Enviar remote request {mesh_id}? [s/N]: "
    approved = (await _prompt(prompt)).casefold() in {"s", "si", "sí", "y", "yes"}
    if not approved:
        print("Solicitud cancelada por el operador.")
    return approved


async def _run_attempt(gateway: SerialGateway, args: argparse.Namespace, session: BenchSession) -> None:
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

    workflow = BenchWorkflow(
        gateway,
        session,
        on_status=lambda message: print(f"\n{message}"),
        on_message=lambda raw: print(f"\n  RX {raw}"),
    )

    async def approve(remaining: float, advisory: bool) -> bool:
        return await _approve_request(remaining, advisory, selected)

    outcome = await workflow.test_selected(
        selected,
        identify_timeout=getattr(args, "identify_timeout", 5.0),
        link_timeout=args.link_timeout,
        link_settle_seconds=args.link_settle_seconds,
        response_timeout=args.response_timeout,
        request_approval=approve,
        rpm_min=args.rpm_min,
        rpm_max=args.rpm_max,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
    )
    if outcome.elapsed is not None and session.telemetry is not None and session.result:
        fields = _metrics(session.telemetry)
        print(f"MAC: {session.mac_address or 'No recibida'}")
        print(f"Device-ID: {session.device_id or 'No informado'}")
        print(f"RPM: {fields.get('rpm')}")
        print(f"Temperaturas: {fields.get('temperature')}")
        print(f"Alarmas: {fields.get('alarms')}")


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
