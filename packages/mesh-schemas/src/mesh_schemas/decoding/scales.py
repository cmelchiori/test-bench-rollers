"""Numeric scale conversions for quantized telemetry fields."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict


class DecodeParams(BaseModel):
    model_config = ConfigDict(frozen=True)
    min_val: float
    max_val: float
    max_code: float


def decode_linear(code: int, params: DecodeParams) -> float:
    return code * (params.max_val - params.min_val) / params.max_code + params.min_val


def decode_sqrt(code: int, params: DecodeParams) -> float:
    ratio = code / params.max_code
    return ratio * ratio * (params.max_val - params.min_val) + params.min_val


def decode_cuberoot(code: int, params: DecodeParams) -> float:
    ratio = code / params.max_code
    return ratio**3 * (params.max_val - params.min_val) + params.min_val


def decode_log(code: int, params: DecodeParams) -> float:
    return math.exp(code * math.log(params.max_val / params.min_val) / params.max_code) * params.min_val
