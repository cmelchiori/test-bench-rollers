"""Schema for the 124-byte ``roller-data-1.3`` telemetry frame."""

from typing import Final

from pydantic import BaseModel, Field

from .roller_data_v11 import (
    OdometerMetrics,
    PowerMetrics,
    RollerDataMetadata,
    RollerDataTags,
)
from .telemetry import TelemetryPayload

ROLLER_DATA_V13_SCHEMA: Final = "roller-data-1.3"


class LatchedAlarmsV13(BaseModel):
    temp_high: bool
    rpm_high: bool
    audio_high: bool
    axial_high: bool
    radial_high: bool
    vin_low: bool


class NonLatchedAlarmsV13(BaseModel):
    audio_capture: bool
    accel_capture: bool


class AlarmsV13(BaseModel):
    code: int
    latched: LatchedAlarmsV13
    non_latched: NonLatchedAlarmsV13


class Harmonic(BaseModel):
    frequency: float
    amplitude: float
    amplitude_percent: float


class Statistics(BaseModel):
    rms: float
    crest_factor: float
    kurtosis: float


class RadialStatistics(Statistics):
    axis_preference: float
    cross_coupling: float


class PeakHarmonics(BaseModel):
    peak_amplitude: float
    harmonics: list[Harmonic]
    statistics: Statistics


class RadialPeakHarmonics(BaseModel):
    peak_amplitude: float
    harmonics: list[Harmonic]
    statistics: RadialStatistics


class AudioMetrics(PeakHarmonics):
    rpm: int


class Tilt(BaseModel):
    axial: float
    radial: float


class HorometerMetrics(BaseModel):
    session: int
    total: int


class AccelerationMetrics(BaseModel):
    axial: PeakHarmonics
    radial: RadialPeakHarmonics
    rpm: int
    angle: Tilt


class RollerDataV13Fields(BaseModel):
    alarms: AlarmsV13
    power: PowerMetrics
    rpm: int
    temperature: list[int | None]
    audio: AudioMetrics
    accel: AccelerationMetrics
    odometer: OdometerMetrics
    horometer: HorometerMetrics
    service_cycles: int


class RollerDataV13(TelemetryPayload[RollerDataTags, RollerDataV13Fields, RollerDataMetadata]):
    """Typed normalized result of a ``roller-data-1.3`` payload."""

    schema_name: str = Field(
        ROLLER_DATA_V13_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
