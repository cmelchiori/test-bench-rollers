"""Shared binary layout for the common roller telemetry frame."""

from __future__ import annotations

from ..decoding import BitsDecoder, DecodeParams, HarmonicParams, decode_harmonics, decode_linear, decode_sqrt

ROLLER_DATA_V11_ENCODED_LENGTH = 120
ROLLER_DATA_V11_DECODED_LENGTH = 96

RPM_PARAMS = DecodeParams(min_val=0.0, max_val=1500.0, max_code=1023)
VIN_PARAMS = DecodeParams(min_val=0.0, max_val=51.0, max_code=255)
VDD_PARAMS = DecodeParams(min_val=1.76, max_val=3.65, max_code=63)
TEMP_PARAMS = DecodeParams(min_val=-55.0, max_val=200.0, max_code=255)
AUDIO_PARAMS = HarmonicParams(
    peak=DecodeParams(min_val=10.0, max_val=400.0, max_code=255),
    amp=DecodeParams(min_val=0.0, max_val=1.0, max_code=1023),
    freq=DecodeParams(min_val=200.0, max_val=12500.0, max_code=1023),
)
ACCEL_PARAMS = HarmonicParams(
    peak=DecodeParams(min_val=0.4, max_val=16.0, max_code=255),
    amp=DecodeParams(min_val=0.0, max_val=1.0, max_code=1023),
    freq=DecodeParams(min_val=3.0, max_val=235.0, max_code=1023),
)


def decode_common_roller_data(decoded: bytes) -> tuple[str, dict[str, object]] | None:
    """Decode the 96-byte field set shared by ``roller-data-1.1`` and ``1.2``."""
    try:
        decoder = BitsDecoder(bits="".join(f"{byte:08b}" for byte in decoded))
        alarms_latched = decoder.extract_int(8)
        device_raw = decoder.extract_int(48).to_bytes(6, "little")
        device_id = f"0x{int.from_bytes(device_raw, 'big'):X}"
        fields = decode_roller_data_fields(decoder)
        if decoder.bits:
            return None
    except (OverflowError, ValueError):
        return None
    return (
        device_id,
        {
            "alarms": {"latched": {"all": alarms_latched}},
            **fields,
        },
    )


def decode_roller_data_fields(decoder: BitsDecoder) -> dict[str, object]:
    """Decode the measurement fields following a roller telemetry frame header."""
    rpm = round(decode_sqrt(decoder.extract_int(10), RPM_PARAMS))
    vin = round(decode_linear(decoder.extract_int(8), VIN_PARAMS), 2)
    vdd = round(decode_linear(decoder.extract_int(6), VDD_PARAMS), 2)
    raw_temps = [round(decode_linear(decoder.extract_int(8), TEMP_PARAMS)) for _ in range(4)]
    audio = decode_harmonics(decoder, AUDIO_PARAMS)
    accel_axial = decode_harmonics(decoder, ACCEL_PARAMS)
    accel_radial = decode_harmonics(decoder, ACCEL_PARAMS)
    odometer_session = decoder.extract_int(32)
    odometer_total = decoder.extract_int(32)
    horometer_session = decoder.extract_int(16)
    horometer_total = decoder.extract_int(32)
    horometer_cycles = decoder.extract_int(10)
    return {
        "power": {"vin": vin, "vdd": vdd},
        "rpm": int(rpm),
        "temperature": [None if value == -55 else value for value in raw_temps],
        "audio": audio.model_dump(),
        "accel": {"axial": accel_axial.model_dump(), "radial": accel_radial.model_dump()},
        "odometer": {"session": odometer_session, "total": odometer_total},
        "horometer": {
            "session": horometer_session,
            "total": horometer_total,
            "cycles": horometer_cycles,
        },
    }
