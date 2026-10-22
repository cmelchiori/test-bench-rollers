from __future__ import annotations

import pytest
from pydantic import BaseModel

from mesh_schemas import (
    ROLLER_DATA_V10_SCHEMA,
    ROLLER_DATA_V11_SCHEMA,
    ROLLER_DATA_V12_SCHEMA,
    DecoderRegistry,
    RollerDataV10,
    RollerDataV11,
    RollerDataV12,
    decode_base85_payload,
)

Z85_TABLE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?&<>()[]{}@%$#"


def encode_z85(data: bytes) -> str:
    encoded: list[str] = []
    for index in range(0, len(data), 4):
        word = int.from_bytes(data[index : index + 4], "little")
        for _ in range(5):
            encoded.append(Z85_TABLE[word % 85])
            word //= 85
    return "".join(encoded)


def packed_roller_data(*, alarms: int, rpm: int, vin: int, vdd: int, axial_frequency: int, audio_rms: int = 0) -> str:
    fields = [(alarms, 8), (0x060504030201, 48), (rpm, 10), (vin, 8), (vdd, 6), (0, 8 * 4)]
    for index, first_frequency in enumerate((0, axial_frequency, 0)):
        fields.extend([(0, 8), (audio_rms if index == 0 else 0, 10)])
        for harmonic in range(8):
            fields.extend([(first_frequency if harmonic == 0 else 0, 10), (0, 10)])
    fields.extend([(0, 32), (0, 32), (0, 16), (0, 32), (1023, 10)])
    bits = "".join(f"{value:0{length}b}" for value, length in fields)
    assert len(bits) == 96 * 8
    return encode_z85(int(bits, 2).to_bytes(96, "big"))


def packed_roller_data_extended(
    *,
    audio_statistics: tuple[int, int, int] = (1023, 250, 250),
    axial_statistics: tuple[int, int, int] = (1023, 250, 250),
    radial_statistics: tuple[int, int, int] = (1023, 250, 250),
    axis_preference: int = 100,
    cross_coupling: int = 100,
) -> str:
    audio_rms, audio_crest, audio_kurtosis = audio_statistics
    axial_rms, axial_crest, axial_kurtosis = axial_statistics
    radial_rms, radial_crest, radial_kurtosis = radial_statistics
    fields = [
        (1440, 11),
        (720, 11),
        (audio_rms, 10),
        (audio_crest, 8),
        (audio_kurtosis, 8),
        (axial_rms, 10),
        (axial_crest, 8),
        (axial_kurtosis, 8),
        (radial_rms, 10),
        (radial_crest, 8),
        (radial_kurtosis, 8),
        (axis_preference, 7),
        (cross_coupling, 7),
        (4000, 12),
        *[(1023, 10)] * 5,
        *[(255, 8)] * 10,
    ]
    bits = "".join(f"{value:0{length}b}" for value, length in fields)
    assert len(bits) == 32 * 8
    standard = packed_roller_data(alarms=0, rpm=0, vin=0, vdd=0, axial_frequency=0)
    return encode_z85(decode_z85_for_test(standard) + int(bits, 2).to_bytes(32, "big"))


def decode_z85_for_test(encoded: str) -> bytes:
    decoded: list[int] = []
    for index in range(0, len(encoded), 5):
        word = 0
        for offset in range(5):
            word = word * 85 + Z85_TABLE.index(encoded[index + 4 - offset])
        decoded.extend(word.to_bytes(4, "little"))
    return bytes(decoded)


def test_decodes_roller_data_v11_to_a_typed_schema() -> None:
    encoded = packed_roller_data(
        alarms=0b10100101,
        rpm=1023,
        vin=255,
        vdd=63,
        axial_frequency=1023,
        audio_rms=1023,
    )

    decoded = decode_base85_payload(
        encoded,
        schema=ROLLER_DATA_V11_SCHEMA,
    )

    assert isinstance(decoded, RollerDataV11)
    assert decoded.schema_name == "roller-data-1.1"
    assert decoded.model_dump(by_alias=True)["schema"] == "roller-data-1.1"
    assert decoded.tags.device_id == "0x10203040506"
    assert decoded.tags.model_dump() == {"device_id": "0x10203040506"}
    assert decoded.fields.alarms.latched.all == 0b10100101
    assert decoded.fields.rpm == 1500
    assert decoded.fields.power.vin == 51.0
    assert decoded.fields.power.vdd == 3.65
    assert decoded.fields.temperature == [None, None, None, None]
    assert decoded.fields.audio.rms == 40.0
    assert decoded.fields.accel.axial.freq[0] == 235.0
    assert decoded.fields.horometer.cycles == 1023
    assert decoded.metadata.decoder_version == "1.0.0"


def test_decodes_legacy_roller_data_v10_to_a_typed_schema() -> None:
    encoded = packed_roller_data(
        alarms=0b10100101,
        rpm=1023,
        vin=255,
        vdd=63,
        axial_frequency=1023,
        audio_rms=1023,
    )

    decoded = decode_base85_payload(encoded, schema=ROLLER_DATA_V10_SCHEMA)

    assert isinstance(decoded, RollerDataV10)
    assert decoded.schema_name == ROLLER_DATA_V10_SCHEMA
    assert decoded.tags.model_dump() == {"device_id": "0x10203040506"}
    assert decoded.metadata.model_dump() == {
        "decoder_version": "1.0.0",
        "hardware_version": "10",
        "firmware_version": "5",
    }
    assert decoded.fields.model_dump() == {
        "power": {"vin": 51.0, "vdd": 3.65},
        "rpm": 1500,
        "temperature": [None, None, None, None],
        "odometer": {"session": 0, "total": 0},
        "horometer": {"session": 0, "total": 0, "cycles": 1023},
        "audio": {
            "scale": 10.0,
            "rms": 40.0,
            "amplitude": [0.0] * 8,
            "freq": [200.0] * 8,
        },
        "accel": {
            "axial": {
                "scale": 0.4,
                "rms": 0.0,
                "amplitude": [0.0] * 8,
                "freq": [235.0] + [3.0] * 7,
            },
            "radial": {
                "scale": 0.4,
                "rms": 0.0,
                "amplitude": [0.0] * 8,
                "freq": [3.0] * 8,
            },
        },
    }


def test_rejects_unknown_or_incomplete_payloads() -> None:
    encoded = packed_roller_data(alarms=0, rpm=0, vin=0, vdd=0, axial_frequency=0)

    minimum = decode_base85_payload(encoded, schema=ROLLER_DATA_V11_SCHEMA)
    assert isinstance(minimum, RollerDataV11)
    assert minimum.fields.rpm == 0
    assert minimum.fields.power.vin == 0.0
    assert minimum.fields.power.vdd == 1.76

    assert decode_base85_payload("not z85 whitespace", schema=ROLLER_DATA_V11_SCHEMA) is None
    assert decode_base85_payload(encoded[:-1], schema=ROLLER_DATA_V11_SCHEMA) is None
    assert decode_base85_payload(encoded + "00000", schema=ROLLER_DATA_V11_SCHEMA) is None
    assert decode_base85_payload(encoded, schema="unknown") is None
    assert decode_base85_payload(encoded[:-1], schema=ROLLER_DATA_V10_SCHEMA) is None
    assert decode_base85_payload(encoded + "00000", schema=ROLLER_DATA_V10_SCHEMA) is None
    assert decode_base85_payload(" " + encoded, schema=ROLLER_DATA_V10_SCHEMA) is None


def test_decodes_roller_data_v12_to_a_typed_schema() -> None:
    decoded = decode_base85_payload(packed_roller_data_extended(), schema=ROLLER_DATA_V12_SCHEMA)

    assert isinstance(decoded, RollerDataV12)
    assert decoded.schema_name == ROLLER_DATA_V12_SCHEMA
    assert decoded.fields.audio.statistics.model_dump() == {"rms": 300.0, "crest_factor": 25.0, "kurtosis": 25.0}
    assert decoded.fields.accel.angle.model_dump() == {"axial": 180.0, "radial": 0.0}
    assert decoded.fields.accel.axial.statistics.model_dump() == {"rms": 4.0, "crest_factor": 25.0, "kurtosis": 25.0}
    assert decoded.fields.accel.radial.statistics.model_dump() == {
        "rms": 4.0,
        "crest_factor": 25.0,
        "kurtosis": 25.0,
        "axis_preference": 1.0,
        "cross_coupling": 1.0,
    }
    assert decoded.fields.accel.bearing.rpm == 1000.0
    assert decoded.fields.accel.bearing.frequency.model_dump() == pytest.approx(
        {
            "bpfo": 250.0,
            "bpfi": 250.0,
            "ftf": 250.0,
            "bsf": 250.0,
            "bdf": 250.0,
        }
    )
    assert decoded.fields.accel.bearing.amplitude["axial"].model_dump() == {
        "bpfo": 140.0,
        "bpfi": 140.0,
        "ftf": 140.0,
        "bsf": 140.0,
        "bdf": 140.0,
    }
    assert decode_base85_payload(packed_roller_data_extended()[:-1], schema=ROLLER_DATA_V12_SCHEMA) is None


def test_decodes_datax_statistics_and_radial_metrics_with_the_new_scaling() -> None:
    decoded = decode_base85_payload(
        packed_roller_data_extended(
            audio_statistics=(0, 128, 64),
            axial_statistics=(0, 64, 128),
            radial_statistics=(0, 191, 255),
            axis_preference=25,
            cross_coupling=75,
        ),
        schema=ROLLER_DATA_V12_SCHEMA,
    )

    assert isinstance(decoded, RollerDataV12)
    assert decoded.fields.audio.statistics.model_dump() == {"rms": 0.0, "crest_factor": 12.8, "kurtosis": 6.4}
    assert decoded.fields.accel.axial.statistics.model_dump() == {"rms": 0.0, "crest_factor": 6.4, "kurtosis": 12.8}
    assert decoded.fields.accel.radial.statistics.model_dump() == {
        "rms": 0.0,
        "crest_factor": 19.1,
        "kurtosis": 25.5,
        "axis_preference": -0.5,
        "cross_coupling": 0.5,
    }


def test_custom_decoder_can_be_registered_without_editing_the_default_registry() -> None:
    class ExamplePayload(BaseModel):
        value: str

    class ExampleDecoder:
        schema = "example-1.0"

        def decode(self, encoded: str) -> ExamplePayload:
            return ExamplePayload(value=encoded)

    registry = DecoderRegistry()
    registry.register(ExampleDecoder())

    decoded = decode_base85_payload("example", schema="example-1.0", registry=registry)

    assert isinstance(decoded, ExamplePayload)
    assert decoded.value == "example"
