"""Extensible Base85 decoder registry."""

from __future__ import annotations

from importlib import import_module

from pydantic import BaseModel

from .registry import DEFAULT_BASE85_REGISTRY, DecoderRegistry


def decode_base85_payload(
    encoded: str,
    *,
    schema: str,
    registry: DecoderRegistry = DEFAULT_BASE85_REGISTRY,
) -> BaseModel | None:
    """Decode ``encoded`` with the decoder explicitly selected by ``schema``."""
    decoder = registry.get(schema)
    if decoder is None:
        return None
    return decoder.decode(encoded)


# Importing built-in modules registers their decoders through ``@register_decoder``.
for _decoder_module in (
    ".decoders.roller_data_v10",
    ".decoders.roller_data_v11",
    ".decoders.roller_data_v12",
):
    import_module(_decoder_module, package=__package__)
