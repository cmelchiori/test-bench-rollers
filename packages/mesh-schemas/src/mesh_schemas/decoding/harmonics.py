"""Reusable decoder for packed harmonic measurements."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .bits import BitsDecoder
from .scales import DecodeParams, decode_linear, decode_log


class HarmonicParams(BaseModel):
    model_config = ConfigDict(frozen=True)
    peak: DecodeParams
    amp: DecodeParams
    freq: DecodeParams


class HarmonicValues(BaseModel):
    model_config = ConfigDict(frozen=True)
    scale: float
    rms: float
    amplitude: list[float]
    freq: list[float]


def decode_harmonics(
    decoder: BitsDecoder,
    params: HarmonicParams,
) -> HarmonicValues:
    """Decode the common peak, RMS, amplitude and frequency harmonic layout."""
    scale = decode_log(decoder.extract_int(8), params.peak)
    rms = round(4.0 * scale * decode_linear(decoder.extract_int(10), params.amp), 3)
    values = decoder.extract_array(10, 16)
    return HarmonicValues(
        scale=scale,
        rms=rms,
        amplitude=[round(scale * decode_linear(code, params.amp), 4) for code in values[1::2]],
        freq=[round(decode_log(code, params.freq), 2) for code in values[::2]],
    )
