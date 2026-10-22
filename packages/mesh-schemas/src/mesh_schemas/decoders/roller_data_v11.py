"""Z85 decoder for the ``roller-data-1.1`` payload."""

from __future__ import annotations

from ..encoding import decode_z85_frame
from ..registry import register_decoder
from ..schemas import ROLLER_DATA_V11_SCHEMA, RollerDataV11
from .roller_data_common import (
    ROLLER_DATA_V11_DECODED_LENGTH,
    ROLLER_DATA_V11_ENCODED_LENGTH,
    decode_common_roller_data,
)


@register_decoder
class RollerDataV11Decoder:
    """Decode complete 96-byte ``roller-data-1.1`` frames in Z85 wire order."""

    schema = ROLLER_DATA_V11_SCHEMA

    def decode(self, encoded: str) -> RollerDataV11 | None:
        decoded = decode_z85_frame(
            encoded,
            encoded_length=ROLLER_DATA_V11_ENCODED_LENGTH,
            decoded_length=ROLLER_DATA_V11_DECODED_LENGTH,
        )
        if decoded is None or (common := decode_common_roller_data(decoded)) is None:
            return None
        device_id, fields = common
        return RollerDataV11.model_validate(
            {
                "tags": {"device_id": device_id},
                "fields": fields,
                "metadata": {},
            }
        )
