"""Command-line entry point for the roller test bench."""

from __future__ import annotations

import argparse
import asyncio
import math

from .evaluation import HARDWARE_ALERT_TEMP_C, evaluate

__all__ = [
    "HARDWARE_ALERT_TEMP_C",
    "_prompt",
    "_run_attempt",
    "_run_terminal",
    "_scan",
    "_send_unlink_group",
    "evaluate",
    "main",
    "show_status",
]

from .terminal_workflow import (
    _prompt,
    _run_attempt,
    _run_terminal,
    _scan,
    _send_unlink_group,
    show_status,
)


def _non_negative(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("debe ser un número finito mayor o igual a cero")
    return parsed


def _positive(value: str) -> float:
    parsed = _non_negative(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return parsed


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("debe ser un número finito")
    return parsed


def _validate_limits(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.rpm_min is not None and args.rpm_max is not None and args.rpm_min > args.rpm_max:
        parser.error("--rpm-min no puede superar --rpm-max")
    if (
        args.temperature_min is not None
        and args.temperature_max is not None
        and args.temperature_min > args.temperature_max
    ):
        parser.error("--temperature-min no puede superar --temperature-max")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba individual de nodos roller por terminal")
    parser.add_argument("--port", required=True, help="Puerto serial, por ejemplo /dev/ttyUSB0")
    parser.add_argument("--ui", choices=("tui", "cli"), default="tui", help="Interfaz de terminal")
    parser.add_argument(
        "--remote-port",
        type=int,
        default=8765,
        help="Puerto de la consola UART remota (8765 por defecto)",
    )
    parser.add_argument(
        "--remote-host",
        default="127.0.0.1",
        help="Dirección de escucha: loopback o IP Tailscale local",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--scan-seconds", type=_positive, default=8.0)
    parser.add_argument("--link-timeout", type=_positive, default=10.0)
    parser.add_argument("--identify-timeout", type=_positive, default=5.0)
    parser.add_argument("--link-settle-seconds", type=_non_negative, default=0.5)
    parser.add_argument("--response-timeout", type=_positive, default=10.0)
    parser.add_argument(
        "--unlink-listen-seconds",
        type=_non_negative,
        default=3.0,
        help="Tiempo para mostrar mensajes recibidos tras remote unlink group (3 s por defecto)",
    )
    parser.add_argument("--rpm-min", type=_non_negative, default=None)
    parser.add_argument("--rpm-max", type=_positive, default=None)
    parser.add_argument("--temperature-min", type=_finite_float, default=None)
    parser.add_argument("--temperature-max", type=_finite_float, default=None)
    args = parser.parse_args()
    _validate_limits(args, parser)
    try:
        if args.ui == "tui":
            from .tui import run_tui

            run_tui(args)
        else:
            asyncio.run(_run_terminal(args))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"No se pudo iniciar la conexión serial: {exc}\n")
    except KeyboardInterrupt:
        print("\nPrueba cancelada por el operador.")


if __name__ == "__main__":
    main()
