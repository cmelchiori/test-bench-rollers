"""Schema for the legacy ``roller-data-1.0`` telemetry frame."""

from typing import Final, Literal

from pydantic import BaseModel, Field

from .roller_data_v11 import AccelerationMetrics, Harmonics, RollerDataBaseFields, RollerDataTags
from .telemetry import TelemetryPayload

ROLLER_DATA_V10_SCHEMA: Final = "roller-data-1.0"


class RollerDataV10Metadata(BaseModel):
    """Firmware versions encoded in the legacy frame header."""

    decoder_version: Literal["1.0.0"] = "1.0.0"
    hardware_version: str
    firmware_version: str


class RollerDataV10Fields(RollerDataBaseFields):
    """Measurements carried by the legacy frame after its version header."""

    audio: Harmonics
    accel: AccelerationMetrics


class RollerDataV10(TelemetryPayload[RollerDataTags, RollerDataV10Fields, RollerDataV10Metadata]):
    """Typed normalized result of a legacy ``roller-data-1.0`` payload."""

    schema_name: str = Field(
        ROLLER_DATA_V10_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
