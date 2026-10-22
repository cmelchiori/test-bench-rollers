"""Schema for the complete ``roller-data-1.1`` telemetry frame."""

from typing import Final, Literal

from pydantic import BaseModel, Field

from .telemetry import TelemetryPayload

ROLLER_DATA_V11_SCHEMA: Final = "roller-data-1.1"


class LatchedAlarms(BaseModel):
    all: int


class Alarms(BaseModel):
    latched: LatchedAlarms


class PowerMetrics(BaseModel):
    vin: float
    vdd: float


class Harmonics(BaseModel):
    scale: float
    rms: float
    amplitude: list[float]
    freq: list[float]


class AccelerationMetrics(BaseModel):
    axial: Harmonics
    radial: Harmonics


class OdometerMetrics(BaseModel):
    session: int
    total: int


class HorometerMetrics(BaseModel):
    session: int
    total: int
    cycles: int


class RollerDataBaseFields(BaseModel):
    power: PowerMetrics
    rpm: int
    temperature: list[int | None]
    odometer: OdometerMetrics
    horometer: HorometerMetrics


class RollerDataFields(RollerDataBaseFields):
    """All decoded values for a roller node."""

    alarms: Alarms
    audio: Harmonics
    accel: AccelerationMetrics


class RollerDataTags(BaseModel):
    device_id: str


class RollerDataMetadata(BaseModel):
    decoder_version: Literal["1.0.0"] = "1.0.0"


RollerDataV11Tags = RollerDataTags
RollerDataV11Metadata = RollerDataMetadata


class RollerDataV11(TelemetryPayload[RollerDataTags, RollerDataFields, RollerDataMetadata]):
    """Typed normalized result of a ``roller-data-1.1`` payload."""

    schema_name: str = Field(
        ROLLER_DATA_V11_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
