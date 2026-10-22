"""Versioned Base85 telemetry schemas and decoders for Mesh Gateway."""

from .base85 import decode_base85_payload
from .registry import DEFAULT_BASE85_REGISTRY, Base85Decoder, DecoderRegistry, register_decoder
from .schemas import (
    ROLLER_DATA_V10_SCHEMA,
    ROLLER_DATA_V11_SCHEMA,
    ROLLER_DATA_V12_SCHEMA,
    RollerDataMetadata,
    RollerDataPayload,
    RollerDataTags,
    RollerDataV10,
    RollerDataV10Fields,
    RollerDataV10Metadata,
    RollerDataV11,
    RollerDataV11Metadata,
    RollerDataV11Tags,
    RollerDataV12,
    TelemetryPayload,
)

__all__ = [
    "DEFAULT_BASE85_REGISTRY",
    "ROLLER_DATA_V10_SCHEMA",
    "ROLLER_DATA_V11_SCHEMA",
    "ROLLER_DATA_V12_SCHEMA",
    "Base85Decoder",
    "DecoderRegistry",
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
    "decode_base85_payload",
    "register_decoder",
]
