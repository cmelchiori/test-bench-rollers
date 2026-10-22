"""Z85 decoder for the extended ``roller-data-1.2`` payload."""

from __future__ import annotations

from ..decoding import BitsDecoder, DecodeParams, decode_cuberoot, decode_linear, decode_log
from ..encoding import decode_z85_frame
from ..registry import register_decoder
from ..schemas import ROLLER_DATA_V12_SCHEMA, RollerDataV12
from .roller_data_common import ROLLER_DATA_V11_DECODED_LENGTH, decode_common_roller_data

ROLLER_DATA_V12_ENCODED_LENGTH = 160
ROLLER_DATA_V12_DECODED_LENGTH = 128
TILT_PARAMS = DecodeParams(min_val=-180.0, max_val=180.0, max_code=1440)
BEARING_RPM_PARAMS = DecodeParams(min_val=0.0, max_val=1000.0, max_code=4000)
BEARING_FREQUENCY_PARAMS = DecodeParams(min_val=2.5, max_val=250.0, max_code=1023)
BEARING_AMPLITUDE_PARAMS = DecodeParams(min_val=0.0, max_val=140.0, max_code=255)
AUDIO_RMS_PARAMS = DecodeParams(min_val=0.0, max_val=300.0, max_code=1023)
ACCELERATION_RMS_PARAMS = DecodeParams(min_val=0.0, max_val=4.0, max_code=1023)
CREST_FACTOR_PARAMS = DecodeParams(min_val=0.0, max_val=25.0, max_code=250)
NORMALIZED_KURTOSIS_PARAMS = DecodeParams(min_val=0.0, max_val=25.0, max_code=250)
AXIS_PREFERENCE_PARAMS = DecodeParams(min_val=-1.0, max_val=1.0, max_code=100)
CROSS_COUPLING_PARAMS = DecodeParams(min_val=-1.0, max_val=1.0, max_code=100)

BEARING_LABELS = ("bpfo", "bpfi", "ftf", "bsf", "bdf")


def _decode_statistics(
    decoder: BitsDecoder,
    *,
    rms_params: DecodeParams,
) -> dict[str, float]:
    return {
        "rms": decode_cuberoot(decoder.extract_int(10), rms_params),
        "crest_factor": decode_linear(decoder.extract_int(8), CREST_FACTOR_PARAMS),
        "kurtosis": decode_linear(decoder.extract_int(8), NORMALIZED_KURTOSIS_PARAMS),
    }


def _decode_bearing_frequencies(decoder: BitsDecoder) -> dict[str, float]:
    return {label: decode_log(decoder.extract_int(10), BEARING_FREQUENCY_PARAMS) for label in BEARING_LABELS}


def _decode_bearing_amplitudes(decoder: BitsDecoder) -> dict[str, float]:
    return {label: decode_cuberoot(decoder.extract_int(8), BEARING_AMPLITUDE_PARAMS) for label in BEARING_LABELS}


@register_decoder
class RollerDataV12Decoder:
    """Decode complete 128-byte ``roller-data-1.2`` DATAX frames in Z85 wire order."""

    schema = ROLLER_DATA_V12_SCHEMA

    def decode(self, encoded: str) -> RollerDataV12 | None:
        decoded = decode_z85_frame(
            encoded,
            encoded_length=ROLLER_DATA_V12_ENCODED_LENGTH,
            decoded_length=ROLLER_DATA_V12_DECODED_LENGTH,
        )
        if decoded is None or (common := decode_common_roller_data(decoded[:ROLLER_DATA_V11_DECODED_LENGTH])) is None:
            return None
        device_id, fields = common
        try:
            decoder = BitsDecoder(bits="".join(f"{byte:08b}" for byte in decoded[ROLLER_DATA_V11_DECODED_LENGTH:]))
            tilt = {
                "axial": decode_linear(decoder.extract_int(11), TILT_PARAMS),
                "radial": decode_linear(decoder.extract_int(11), TILT_PARAMS),
            }
            audio_statistics = _decode_statistics(
                decoder,
                rms_params=AUDIO_RMS_PARAMS,
            )
            axial_statistics = _decode_statistics(
                decoder,
                rms_params=ACCELERATION_RMS_PARAMS,
            )
            radial_statistics = {
                **_decode_statistics(
                    decoder,
                    rms_params=ACCELERATION_RMS_PARAMS,
                ),
                "axis_preference": decode_linear(decoder.extract_int(7), AXIS_PREFERENCE_PARAMS),
                "cross_coupling": decode_linear(decoder.extract_int(7), CROSS_COUPLING_PARAMS),
            }
            bearing = {
                "rpm": decode_linear(decoder.extract_int(12), BEARING_RPM_PARAMS),
                "frequency": _decode_bearing_frequencies(decoder),
                "amplitude": {
                    "axial": _decode_bearing_amplitudes(decoder),
                    "radial": _decode_bearing_amplitudes(decoder),
                },
            }
            if decoder.bits:
                return None
        except (OverflowError, ValueError):
            return None

        audio = fields["audio"]
        accel = fields["accel"]
        assert isinstance(audio, dict) and isinstance(accel, dict)
        axial = accel["axial"]
        radial = accel["radial"]
        assert isinstance(axial, dict) and isinstance(radial, dict)
        fields["audio"] = {**audio, "statistics": audio_statistics}
        fields["accel"] = {
            "axial": {**axial, "statistics": axial_statistics},
            "radial": {**radial, "statistics": radial_statistics},
            "angle": tilt,
            "bearing": bearing,
        }
        return RollerDataV12.model_validate(
            {
                "tags": {"device_id": device_id},
                "fields": fields,
                "metadata": {},
            }
        )
