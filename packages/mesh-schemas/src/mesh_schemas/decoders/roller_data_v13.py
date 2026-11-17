"""Z85 decoder for the 124-byte ``roller-data-1.3`` payload."""

from __future__ import annotations

from ..decoding import BitsDecoder, DecodeParams, decode_cuberoot, decode_linear, decode_log, decode_sqrt
from ..encoding import decode_z85_frame
from ..registry import register_decoder
from ..schemas import ROLLER_DATA_V13_SCHEMA, RollerDataV13

ROLLER_DATA_V13_ENCODED_LENGTH = 155
ROLLER_DATA_V13_DECODED_LENGTH = 124

RPM_PARAMS = DecodeParams(min_val=0.0, max_val=1000.0, max_code=1023)
VIN_PARAMS = DecodeParams(min_val=0.0, max_val=51.0, max_code=255)
VDD_PARAMS = DecodeParams(min_val=1.76, max_val=3.65, max_code=63)
TEMPERATURE_PARAMS = DecodeParams(min_val=-55.0, max_val=200.0, max_code=255)
AUDIO_PEAK_PARAMS = DecodeParams(min_val=10.0, max_val=400.0, max_code=255)
ACCELERATION_PEAK_PARAMS = DecodeParams(min_val=0.4, max_val=16.0, max_code=255)
AUDIO_FREQUENCY_PARAMS = DecodeParams(min_val=200.0, max_val=12500.0, max_code=1023)
ACCELERATION_FREQUENCY_PARAMS = DecodeParams(min_val=2.0, max_val=235.0, max_code=1023)
AMPLITUDE_PERCENT_PARAMS = DecodeParams(min_val=0.0, max_val=100.0, max_code=1000)
AUDIO_RMS_PARAMS = DecodeParams(min_val=0.0, max_val=300.0, max_code=1023)
ACCELERATION_RMS_PARAMS = DecodeParams(min_val=0.0, max_val=4.0, max_code=1023)
CREST_FACTOR_PARAMS = DecodeParams(min_val=0.0, max_val=25.0, max_code=250)
NORMALIZED_KURTOSIS_PARAMS = DecodeParams(min_val=0.0, max_val=25.0, max_code=250)
AXIS_PREFERENCE_PARAMS = DecodeParams(min_val=-1.0, max_val=1.0, max_code=100)
CROSS_COUPLING_PARAMS = DecodeParams(min_val=-1.0, max_val=1.0, max_code=100)
TILT_PARAMS = DecodeParams(min_val=-180.0, max_val=180.0, max_code=1440)


def _decode_harmonics(
    decoder: BitsDecoder,
    *,
    frequency_params: DecodeParams,
    peak_amplitude: float,
) -> list[dict[str, float]]:
    harmonics: list[dict[str, float]] = []
    for _ in range(10):
        frequency = decode_log(decoder.extract_int(10), frequency_params)
        amplitude_percent = decode_linear(decoder.extract_int(10), AMPLITUDE_PERCENT_PARAMS)
        harmonics.append(
            {
                "frequency": round(frequency, 2),
                "amplitude": peak_amplitude * amplitude_percent / 100.0,
                "amplitude_percent": round(amplitude_percent, 1),
            }
        )
    return harmonics


def _decode_statistics(decoder: BitsDecoder, *, rms_params: DecodeParams) -> dict[str, float]:
    return {
        "rms": decode_cuberoot(decoder.extract_int(10), rms_params),
        "crest_factor": decode_linear(decoder.extract_int(8), CREST_FACTOR_PARAMS),
        "kurtosis": decode_linear(decoder.extract_int(8), NORMALIZED_KURTOSIS_PARAMS),
    }


def _decode_alarms(code: int) -> dict[str, object]:
    return {
        "code": code,
        "latched": {
            "temp_high": bool(code & (1 << 0)),
            "rpm_high": bool(code & (1 << 1)),
            "audio_high": bool(code & (1 << 2)),
            "axial_high": bool(code & (1 << 3)),
            "radial_high": bool(code & (1 << 4)),
            "vin_low": bool(code & (1 << 5)),
        },
        "non_latched": {
            "audio_capture": bool(code & (1 << 6)),
            "accel_capture": bool(code & (1 << 7)),
        },
    }


@register_decoder
class RollerDataV13Decoder:
    """Decode complete 124-byte ``roller-data-1.3`` frames in Mesh Z85 wire order."""

    schema = ROLLER_DATA_V13_SCHEMA

    def decode(self, encoded: str) -> RollerDataV13 | None:
        decoded = decode_z85_frame(
            encoded,
            encoded_length=ROLLER_DATA_V13_ENCODED_LENGTH,
            decoded_length=ROLLER_DATA_V13_DECODED_LENGTH,
        )
        if decoded is None:
            return None

        decoder = BitsDecoder(bits="".join(f"{byte:08b}" for byte in decoded))
        try:
            alarms = decoder.extract_int(8)
            device_raw = decoder.extract_int(48).to_bytes(6, "little")
            device_id = f"0x{int.from_bytes(device_raw, 'big'):X}"
            rpm = round(decode_sqrt(decoder.extract_int(10), RPM_PARAMS))
            vin = round(decode_linear(decoder.extract_int(8), VIN_PARAMS), 2)
            vdd = round(decode_linear(decoder.extract_int(6), VDD_PARAMS), 2)
            temperatures = [
                None
                if (temperature := round(decode_linear(decoder.extract_int(8), TEMPERATURE_PARAMS))) == -55
                else temperature
                for _ in range(4)
            ]

            audio_peak = decode_log(decoder.extract_int(8), AUDIO_PEAK_PARAMS)
            audio_harmonics = _decode_harmonics(
                decoder,
                frequency_params=AUDIO_FREQUENCY_PARAMS,
                peak_amplitude=audio_peak,
            )
            audio_rpm = round(decode_sqrt(decoder.extract_int(10), RPM_PARAMS))

            axial_peak = decode_log(decoder.extract_int(8), ACCELERATION_PEAK_PARAMS)
            axial_harmonics = _decode_harmonics(
                decoder,
                frequency_params=ACCELERATION_FREQUENCY_PARAMS,
                peak_amplitude=axial_peak,
            )
            radial_peak = decode_log(decoder.extract_int(8), ACCELERATION_PEAK_PARAMS)
            radial_harmonics = _decode_harmonics(
                decoder,
                frequency_params=ACCELERATION_FREQUENCY_PARAMS,
                peak_amplitude=radial_peak,
            )
            acceleration_rpm = round(decode_sqrt(decoder.extract_int(10), RPM_PARAMS))

            odometer_session = decoder.extract_int(32)
            odometer_total = decoder.extract_int(32)
            horometer_session = decoder.extract_int(16)
            horometer_total = decoder.extract_int(32)
            service_cycles = decoder.extract_int(10)

            tilt = {
                "axial": decode_linear(decoder.extract_int(11), TILT_PARAMS),
                "radial": decode_linear(decoder.extract_int(11), TILT_PARAMS),
            }
            audio_statistics = _decode_statistics(decoder, rms_params=AUDIO_RMS_PARAMS)
            axial_statistics = _decode_statistics(decoder, rms_params=ACCELERATION_RMS_PARAMS)
            radial_statistics = {
                **_decode_statistics(decoder, rms_params=ACCELERATION_RMS_PARAMS),
                "axis_preference": decode_linear(decoder.extract_int(7), AXIS_PREFERENCE_PARAMS),
                "cross_coupling": decode_linear(decoder.extract_int(7), CROSS_COUPLING_PARAMS),
            }
            if decoder.bits:
                return None
        except (OverflowError, ValueError):
            return None

        return RollerDataV13.model_validate(
            {
                "tags": {"device_id": device_id},
                "fields": {
                    "alarms": _decode_alarms(alarms),
                    "power": {"vin": vin, "vdd": vdd},
                    "rpm": int(rpm),
                    "temperature": temperatures,
                    "audio": {
                        "peak_amplitude": audio_peak,
                        "harmonics": audio_harmonics,
                        "rpm": int(audio_rpm),
                        "statistics": audio_statistics,
                    },
                    "accel": {
                        "axial": {
                            "peak_amplitude": axial_peak,
                            "harmonics": axial_harmonics,
                            "statistics": axial_statistics,
                        },
                        "radial": {
                            "peak_amplitude": radial_peak,
                            "harmonics": radial_harmonics,
                            "statistics": radial_statistics,
                        },
                        "rpm": int(acceleration_rpm),
                        "angle": tilt,
                    },
                    "odometer": {"session": odometer_session, "total": odometer_total},
                    "horometer": {
                        "session": horometer_session,
                        "total": horometer_total,
                    },
                    "service_cycles": service_cycles,
                },
                "metadata": {},
            }
        )
