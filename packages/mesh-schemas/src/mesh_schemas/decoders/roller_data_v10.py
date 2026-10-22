"""Z85 decoder for the legacy ``roller-data-1.0`` payload."""

from __future__ import annotations

from ..decoding import BitsDecoder
from ..encoding import decode_z85_frame
from ..registry import register_decoder
from ..schemas import ROLLER_DATA_V10_SCHEMA, RollerDataV10
from .roller_data_common import (
    ROLLER_DATA_V11_DECODED_LENGTH,
    ROLLER_DATA_V11_ENCODED_LENGTH,
    decode_roller_data_fields,
)


@register_decoder
class RollerDataV10Decoder:
    """Decode complete 96-byte legacy DATA frames in Z85 wire order."""

    schema = ROLLER_DATA_V10_SCHEMA

    def decode(self, encoded: str) -> RollerDataV10 | None:
        decoded = decode_z85_frame(
            encoded,
            encoded_length=ROLLER_DATA_V11_ENCODED_LENGTH,
            decoded_length=ROLLER_DATA_V11_DECODED_LENGTH,
        )
        if decoded is None:
            return None

        try:
            decoder = BitsDecoder(bits="".join(f"{byte:08b}" for byte in decoded))
            hardware_version = str(decoder.extract_int(4))
            firmware_version = str(decoder.extract_int(4))
            device_raw = decoder.extract_int(48).to_bytes(6, "little")
            device_id = f"0x{int.from_bytes(device_raw, 'big'):X}"
            fields = decode_roller_data_fields(decoder)
            if decoder.bits:
                return None
        except (OverflowError, ValueError):
            return None

        return RollerDataV10.model_validate(
            {
                "tags": {"device_id": device_id},
                "fields": fields,
                "metadata": {
                    "hardware_version": hardware_version,
                    "firmware_version": firmware_version,
                },
            }
        )
