"""Telemetry evaluation rules for roller bench results."""

from __future__ import annotations

import math
from typing import Any


HARDWARE_ALERT_TEMP_C = 160


def _metrics(payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields")
    return fields if isinstance(fields, dict) else {}


def evaluate(
    payload: dict[str, Any],
    *,
    rpm_min: float | None = None,
    rpm_max: float | None = None,
    temperature_min: float | None = None,
    temperature_max: float | None = None,
) -> tuple[bool, list[str]]:
    """Evaluate telemetry presence and any configured acceptance limits."""
    metrics = _metrics(payload)
    failures: list[str] = []
    rpm = metrics.get("rpm")
    if not isinstance(rpm, (int, float)) or not math.isfinite(rpm) or rpm <= 0:
        failures.append("RPM ausentes o no positivos")
    else:
        if rpm_min is not None and rpm < rpm_min:
            failures.append(f"RPM bajo el mínimo configurado ({rpm:g} < {rpm_min:g})")
        if rpm_max is not None and rpm > rpm_max:
            failures.append(f"RPM sobre el máximo configurado ({rpm:g} > {rpm_max:g})")

    temperatures = metrics.get("temperature")
    valid_temperatures = (
        [value for value in temperatures if isinstance(value, (int, float)) and math.isfinite(value)]
        if isinstance(temperatures, list)
        else []
    )
    if not valid_temperatures:
        failures.append("No hay temperaturas válidas")
    else:
        for index, value in enumerate(valid_temperatures, start=1):
            if value > HARDWARE_ALERT_TEMP_C:
                failures.append(
                    f"Temperatura {index} sobre el límite crítico de hardware ({value:g} > {HARDWARE_ALERT_TEMP_C} °C)"
                )
            if temperature_min is not None and value < temperature_min:
                failures.append(f"Temperatura {index} bajo el mínimo configurado ({value:g} < {temperature_min:g})")
            if temperature_max is not None and value > temperature_max:
                failures.append(f"Temperatura {index} sobre el máximo configurado ({value:g} > {temperature_max:g})")

    alarms = metrics.get("alarms")
    if isinstance(alarms, dict):
        latched = alarms.get("latched")
        if isinstance(latched, dict) and latched.get("all", 0) != 0:
            failures.append(f"Alarmas activas: {latched['all']}")
    return not failures, failures
