"""Golden outputs copied from apps/decoder, mapped to this project's contract."""

import json
from pathlib import Path

import pytest

from mesh_schemas import ROLLER_DATA_V10_SCHEMA, RollerDataV10, RollerDataV11, RollerDataV12, decode_base85_payload

DATA = (
    "5psf-lk!5yWZ3/p<!200Yo?{2:LW48]qY?Ul&U5Q(2*90ZE*DG!ZSCcNknc0g8tGWIQ]"
    "W0=LB3074vxppivF17:x87@]Yj77j[KPxwcf4<7CU2!&n10*1DGk"
)
DATAX = DATA + "3o}?kJR&IE@+JDUG&A+fBy:n<uhhQ1lkUl2u?3j3"


@pytest.mark.parametrize(
    "encoded,schema,fixture",
    [(DATA, "roller-data-1.1", "data_output.json"), (DATAX, "roller-data-1.2", "datax_output.json")],
)
def test_matches_reference_output(encoded: str, schema: str, fixture: str) -> None:
    reference = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text(encoding="utf-8"))
    device = reference["device"]
    expected = device["metrics"]
    expected["alarms"] = {"latched": {"all": sum(1 << bit for bit in range(8) if device["alarms"][f"b{bit}"])}}
    if encoded == DATAX:
        accel = expected["accel"]
        for harmonics in (expected["audio"], accel["axial"], accel["radial"]):
            stats = harmonics.pop("stat")
            stats["crest_factor"] = stats.pop("crest")
            stats["kurtosis"] = stats.pop("kurt")
            harmonics["statistics"] = stats
        radial = accel["radial"]["statistics"]
        radial["axis_preference"] = radial.pop("disp")
        radial["cross_coupling"] = radial.pop("angle")
        bearing = accel["bearing"]
        bearing["frequency"] = {key.lower(): value for key, value in bearing.pop("freq").items()}
        bearing["amplitude"] = {
            axis: {key.lower(): value for key, value in bearing.pop(f"amp_{axis}").items()}
            for axis in ("axial", "radial")
        }
    decoded = decode_base85_payload(encoded, schema=schema)
    assert isinstance(decoded, (RollerDataV11, RollerDataV12))
    assert decoded.tags.device_id == device["id"]
    assert decoded.fields.model_dump() == expected


def test_legacy_v10_matches_the_reference_data_metrics() -> None:
    reference = json.loads((Path(__file__).parent / "fixtures" / "data_output.json").read_text(encoding="utf-8"))
    device = reference["device"]

    decoded = decode_base85_payload(DATA, schema=ROLLER_DATA_V10_SCHEMA)

    assert isinstance(decoded, RollerDataV10)
    assert decoded.tags.device_id == device["id"]
    assert decoded.fields.model_dump() == device["metrics"]
    assert decoded.metadata.model_dump() == {
        "decoder_version": "1.0.0",
        "hardware_version": "0",
        "firmware_version": "0",
    }


@pytest.mark.parametrize("encoded,schema", [(DATA, "roller-data-1.1"), (DATAX, "roller-data-1.2")])
@pytest.mark.parametrize("padding", [" ", "\n", "\r\n", "\t"])
def test_rejects_whitespace_without_normalizing_payload(encoded: str, schema: str, padding: str) -> None:
    assert decode_base85_payload(padding + encoded, schema=schema) is None
    assert decode_base85_payload(encoded + padding, schema=schema) is None
    assert decode_base85_payload(padding + encoded[len(padding) :], schema=schema) is None


WIDTHS = [11, 11] + [10, 8, 8] * 3 + [7, 7, 12] + [10] * 5 + [8] * 10


def encode_extension(codes: list[int]) -> str:
    from mesh_schemas.encoding import Z85_TABLE

    bits = "".join(f"{code:0{width}b}" for code, width in zip(codes, WIDTHS, strict=True))
    data = int(bits, 2).to_bytes(32, "big")
    output = ""
    for offset in range(0, 32, 4):
        word = int.from_bytes(data[offset : offset + 4], "little")
        for _ in range(5):
            word, digit = divmod(word, 85)
            output += Z85_TABLE[digit]
    return output


def test_extension_resolution_and_small_rms_are_preserved() -> None:
    decoded = decode_base85_payload(DATA + encode_extension([1] * len(WIDTHS)), schema="roller-data-1.2")
    assert isinstance(decoded, RollerDataV12)
    fields = decoded.fields
    assert fields.accel.angle.model_dump() == {"axial": -179.75, "radial": -179.75}
    for stats, maximum in (
        (fields.audio.statistics, 300),
        (fields.accel.axial.statistics, 4),
        (fields.accel.radial.statistics, 4),
    ):
        assert stats.rms == pytest.approx(maximum / 1023**3)
        assert stats.rms > 0
        assert stats.crest_factor == 0.1
        assert stats.kurtosis == 0.1
    assert fields.accel.radial.statistics.axis_preference == -0.98
    assert fields.accel.radial.statistics.cross_coupling == -0.98
    assert fields.accel.bearing.rpm == 0.25


def test_extension_field_order_and_nonlinear_scaling() -> None:
    codes = [0, 1440, 341, 50, 100, 682, 150, 200, 1023, 250, 0, 0, 100, 800]
    codes += [0, 341, 682, 1023, 0]
    codes += [0, 85, 170, 255, 0] + [255, 170, 85, 0, 255]
    decoded = decode_base85_payload(DATA + encode_extension(codes), schema="roller-data-1.2")
    assert isinstance(decoded, RollerDataV12)
    fields = decoded.fields
    assert fields.accel.angle.model_dump() == {"axial": -180, "radial": 180}
    for stats, expected in (
        (fields.audio.statistics, (300 / 27, 5, 10)),
        (fields.accel.axial.statistics, (32 / 27, 15, 20)),
        (fields.accel.radial.statistics, (4, 25, 0)),
    ):
        assert (stats.rms, stats.crest_factor, stats.kurtosis) == pytest.approx(expected)
    assert fields.accel.radial.statistics.axis_preference == -1
    assert fields.accel.radial.statistics.cross_coupling == 1
    bearing = fields.accel.bearing
    assert bearing.rpm == 200
    assert list(bearing.frequency.model_dump().values()) == pytest.approx(
        [2.5, 2.5 * 100 ** (1 / 3), 2.5 * 100 ** (2 / 3), 250, 2.5]
    )
    assert list(bearing.amplitude["axial"].model_dump().values()) == pytest.approx([0, 140 / 27, 1120 / 27, 140, 0])
    assert list(bearing.amplitude["radial"].model_dump().values()) == pytest.approx([140, 1120 / 27, 140 / 27, 0, 140])


def test_datax_preserves_all_data_fields() -> None:
    base = decode_base85_payload(DATA, schema="roller-data-1.1")
    extended = decode_base85_payload(DATA + "0" * 40, schema="roller-data-1.2")
    assert isinstance(base, RollerDataV11)
    assert isinstance(extended, RollerDataV12)
    fields = extended.fields.model_dump()
    del fields["audio"]["statistics"]
    for axis in ("axial", "radial"):
        del fields["accel"][axis]["statistics"]
    del fields["accel"]["angle"]
    del fields["accel"]["bearing"]
    assert fields == base.fields.model_dump()
    assert extended.tags == base.tags


@pytest.mark.parametrize("maximum", [False, True])
def test_extension_endpoints(maximum: bool) -> None:
    codes = [1440, 1440] + [1023, 250, 250] * 3 + [100, 100, 4000] + [1023] * 5 + [255] * 10
    decoded = decode_base85_payload(
        DATA + encode_extension(codes if maximum else [0] * len(WIDTHS)), schema="roller-data-1.2"
    )
    assert isinstance(decoded, RollerDataV12)
    fields = decoded.fields
    assert fields.accel.angle.model_dump() == dict.fromkeys(("axial", "radial"), 180 if maximum else -180)
    for stats, rms_max in (
        (fields.audio.statistics, 300),
        (fields.accel.axial.statistics, 4),
        (fields.accel.radial.statistics, 4),
    ):
        assert stats.rms == (rms_max if maximum else 0)
        assert stats.crest_factor == (25 if maximum else 0)
        assert stats.kurtosis == (25 if maximum else 0)
    assert fields.accel.radial.statistics.axis_preference == (1 if maximum else -1)
    assert fields.accel.radial.statistics.cross_coupling == (1 if maximum else -1)
    bearing = fields.accel.bearing
    assert bearing.rpm == (1000 if maximum else 0)
    assert list(bearing.frequency.model_dump().values()) == pytest.approx([250 if maximum else 2.5] * 5)
    for axis in ("axial", "radial"):
        assert list(bearing.amplitude[axis].model_dump().values()) == [140 if maximum else 0] * 5


@pytest.mark.parametrize("encoded,schema", [(DATA, "roller-data-1.1"), (DATAX, "roller-data-1.2")])
def test_invalid_lengths_and_alphabet(encoded: str, schema: str) -> None:
    for invalid in (encoded[:-1], encoded + "0", "~" + encoded[1:], "", DATA if encoded == DATAX else DATAX):
        assert decode_base85_payload(invalid, schema=schema) is None
