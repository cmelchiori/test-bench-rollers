"""Public, versioned telemetry schemas."""

from .roller_data_v10 import (
    ROLLER_DATA_V10_SCHEMA,
    RollerDataV10,
    RollerDataV10Fields,
    RollerDataV10Metadata,
)
from .roller_data_v11 import (
    ROLLER_DATA_V11_SCHEMA,
    RollerDataMetadata,
    RollerDataTags,
    RollerDataV11,
    RollerDataV11Metadata,
    RollerDataV11Tags,
)
from .roller_data_v12 import ROLLER_DATA_V12_SCHEMA, RollerDataV12
from .telemetry import TelemetryPayload

RollerDataPayload = RollerDataV10 | RollerDataV11 | RollerDataV12

__all__ = [
    "ROLLER_DATA_V10_SCHEMA",
    "ROLLER_DATA_V11_SCHEMA",
    "ROLLER_DATA_V12_SCHEMA",
    "RollerDataMetadata",
    "RollerDataPayload",
    "RollerDataTags",
    "RollerDataV10",
    "RollerDataV10Fields",
    "RollerDataV10Metadata",
    "RollerDataV11",
    "RollerDataV11Metadata",
    "RollerDataV11Tags",
    "RollerDataV12",
    "TelemetryPayload",
]
