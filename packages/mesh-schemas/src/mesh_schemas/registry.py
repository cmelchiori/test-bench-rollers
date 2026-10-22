"""Registration mechanism for independently authored Base85 decoders."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class Base85Decoder(Protocol):
    """Decoder registered for one explicitly selected telemetry schema."""

    schema: str

    def decode(self, encoded: str) -> BaseModel | None: ...


class DecoderRegistry:
    """Base85 decoders indexed by the schema selected by the caller."""

    def __init__(self) -> None:
        self._decoders: dict[str, Base85Decoder] = {}

    def get(self, schema: str) -> Base85Decoder | None:
        return self._decoders.get(schema)

    def register(self, decoder: Base85Decoder) -> Base85Decoder:
        """Register a decoder, returning it so this can be used as a decorator."""
        if decoder.schema in self._decoders:
            raise ValueError(f"a decoder is already registered for {decoder.schema!r}")
        self._decoders[decoder.schema] = decoder
        return decoder


DEFAULT_BASE85_REGISTRY = DecoderRegistry()


def register_decoder(decoder_type: type[Base85Decoder]) -> type[Base85Decoder]:
    """Instantiate and register a built-in decoder without a central list."""
    DEFAULT_BASE85_REGISTRY.register(decoder_type())
    return decoder_type
