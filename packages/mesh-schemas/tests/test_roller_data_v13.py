from __future__ import annotations

import pytest

from mesh_schemas import ROLLER_DATA_V13_SCHEMA, RollerDataV13, decode_base85_payload
from mesh_schemas.encoding import Z85_TABLE, decode_z85

DATAX_V13_WITH_PREFIX = (
    "d]5psf-00000AziepFGluh2MS/ncDrs5?2T$d>jDvF/[W{<^>6Q1St#cFr&cltIyIm0BgXaGMTD=4z%"
    "RZPLKB-G=EEej[{tB4AcjDP9@t]<]D8308nYWqHJ40019rr00C2t0hzr/)g%uAT-UOM99dZBFqtQCb"
)
DATAX_V13 = DATAX_V13_WITH_PREFIX[2:]


def encode_z85(data: bytes) -> str:
    encoded: list[str] = []
    for index in range(0, len(data), 4):
        word = int.from_bytes(data[index : index + 4], "little")
        for _ in range(5):
            word, digit = divmod(word, 85)
            encoded.append(Z85_TABLE[digit])
    return "".join(encoded)


def test_decodes_roller_data_v13_reference_vector() -> None:
    assert len(DATAX_V13) == 155

    decoded = decode_base85_payload(DATAX_V13, schema=ROLLER_DATA_V13_SCHEMA)

    assert isinstance(decoded, RollerDataV13)
    assert decoded.schema_name == "roller-data-1.3"
    assert decoded.tags.device_id == "0xC49456"
    assert decoded.fields.alarms.code == 0
    assert decoded.fields.alarms.latched.model_dump() == {
        "temp_high": False,
        "rpm_high": False,
        "audio_high": False,
        "axial_high": False,
        "radial_high": False,
        "vin_low": False,
    }
    assert decoded.fields.alarms.non_latched.model_dump() == {
        "audio_capture": False,
        "accel_capture": False,
    }
    assert decoded.fields.power.vin == 3.2
    assert decoded.fields.power.vdd == 3.14
    assert decoded.fields.temperature == [23, 23, 24, 23]
    assert decoded.fields.audio.rpm == 0
    assert len(decoded.fields.audio.harmonics) == 10
    assert decoded.fields.audio.harmonics[0].frequency == pytest.approx(484.5395, rel=0.002)
    assert decoded.fields.audio.harmonics[0].amplitude_percent == 0.8
    assert decoded.fields.audio.harmonics[0].amplitude == pytest.approx(0.08)
    assert decoded.fields.audio.statistics.rms == pytest.approx(0.6293285, rel=0.002)
    assert len(decoded.fields.accel.axial.harmonics) == 10
    assert len(decoded.fields.accel.radial.harmonics) == 10
    assert decoded.fields.accel.radial.harmonics[0].frequency == pytest.approx(4.709364, rel=0.002)
    assert decoded.fields.accel.radial.harmonics[0].amplitude == pytest.approx(0.02254554761537926)
    assert decoded.fields.accel.radial.statistics.axis_preference == pytest.approx(0.4076915, abs=0.02)
    assert decoded.fields.accel.radial.statistics.cross_coupling == pytest.approx(-0.3013972, abs=0.02)
    assert decoded.fields.accel.angle.model_dump() == {"axial": 6.75, "radial": -1.5}
    assert decoded.fields.odometer.total == 2_003_025
    assert decoded.fields.service_cycles == 209


def test_v13_alarm_bits_keep_latched_and_capture_flags_separate() -> None:
    raw = bytearray(decode_z85(DATAX_V13))
    raw[0] = 0b11010101

    decoded = decode_base85_payload(encode_z85(raw), schema=ROLLER_DATA_V13_SCHEMA)

    assert isinstance(decoded, RollerDataV13)
    assert decoded.fields.alarms.code == 0b11010101
    assert decoded.fields.alarms.latched.model_dump() == {
        "temp_high": True,
        "rpm_high": False,
        "audio_high": True,
        "axial_high": False,
        "radial_high": True,
        "vin_low": False,
    }
    assert decoded.fields.alarms.non_latched.model_dump() == {
        "audio_capture": True,
        "accel_capture": True,
    }


@pytest.mark.parametrize(
    "encoded",
    [
        DATAX_V13_WITH_PREFIX,
        DATAX_V13[:-1],
        DATAX_V13 + "0",
        " " + DATAX_V13,
    ],
)
def test_rejects_non_frame_v13_payloads(encoded: str) -> None:
    assert decode_base85_payload(encoded, schema=ROLLER_DATA_V13_SCHEMA) is None


def test_v13_schema_is_selected_explicitly() -> None:
    assert decode_base85_payload(DATAX_V13, schema="unknown") is None
