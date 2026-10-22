"""Reusable normalized result emitted by Base85 decoders."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TelemetryPayload[TagsT: BaseModel, FieldsT: BaseModel, MetadataT: BaseModel](BaseModel):
    """Typed, normalized Base85 result without transport serialization concerns."""

    schema_name: str = Field(alias="schema", serialization_alias="schema")
    tags: TagsT
    fields: FieldsT
    metadata: MetadataT
