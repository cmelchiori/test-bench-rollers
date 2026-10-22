"""Schema for the extended ``roller-data-1.2`` telemetry frame."""

from typing import Final

from pydantic import BaseModel, Field

from .roller_data_v11 import (
    Alarms,
    Harmonics,
    RollerDataBaseFields,
    RollerDataMetadata,
    RollerDataTags,
)
from .telemetry import TelemetryPayload

ROLLER_DATA_V12_SCHEMA: Final = "roller-data-1.2"


class Statistics(BaseModel):
    rms: float
    crest_factor: float
    kurtosis: float


class RadialStatistics(Statistics):
    axis_preference: float
    cross_coupling: float


class HarmonicsWithStatistics(Harmonics):
    statistics: Statistics


class HarmonicsWithRadialStatistics(Harmonics):
    statistics: RadialStatistics


class Tilt(BaseModel):
    axial: float
    radial: float


class BearingAmplitudes(BaseModel):
    bpfo: float
    bpfi: float
    ftf: float
    bsf: float
    bdf: float


class BearingFrequencies(BaseModel):
    bpfo: float
    bpfi: float
    ftf: float
    bsf: float
    bdf: float


class BearingMetrics(BaseModel):
    rpm: float
    frequency: BearingFrequencies
    amplitude: dict[str, BearingAmplitudes]


class ExtendedAccelerationMetrics(BaseModel):
    axial: HarmonicsWithStatistics
    radial: HarmonicsWithRadialStatistics
    angle: Tilt
    bearing: BearingMetrics


class RollerDataV12Fields(RollerDataBaseFields):
    """All decoded values for an extended roller node frame."""

    alarms: Alarms
    audio: HarmonicsWithStatistics
    accel: ExtendedAccelerationMetrics


class RollerDataV12(TelemetryPayload[RollerDataTags, RollerDataV12Fields, RollerDataMetadata]):
    """Typed normalized result of a ``roller-data-1.2`` payload."""

    schema_name: str = Field(
        ROLLER_DATA_V12_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
