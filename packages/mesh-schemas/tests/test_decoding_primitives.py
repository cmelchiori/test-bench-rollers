from __future__ import annotations

import math

import pytest

from mesh_schemas.decoding import BitsDecoder, DecodeParams, decode_cuberoot, decode_linear, decode_log, decode_sqrt
from mesh_schemas.encoding import decode_z85, decode_z85_frame


def test_bits_decoder_consumes_fields_in_order() -> None:
    decoder = BitsDecoder(bits="10110100")

    assert decoder.extract_int(3) == 0b101
    assert decoder.extract_array(1, 3) == [1, 0, 1]
    assert decoder.bits == "00"


def test_bits_decoder_rejects_an_incomplete_field() -> None:
    with pytest.raises(ValueError, match="insufficient payload bits"):
        BitsDecoder(bits="1").extract_int(2)


def test_z85_decoder_uses_mesh_little_endian_word_order() -> None:
    assert decode_z85("00000") == b"\x00\x00\x00\x00"
    assert decode_z85("aYOo1") == bytes.fromhex("01020304")
    assert decode_z85_frame("aYOo1", encoded_length=5, decoded_length=4) == bytes.fromhex("01020304")
    assert decode_z85_frame("aYOo", encoded_length=5, decoded_length=4) is None


def test_scale_conversions_are_reusable_across_schemas() -> None:
    linear_params = DecodeParams(min_val=0.0, max_val=10.0, max_code=10)
    log_params = DecodeParams(min_val=1.0, max_val=100.0, max_code=2)

    assert decode_linear(5, linear_params) == 5.0
    assert decode_sqrt(5, linear_params) == 2.5
    assert decode_cuberoot(5, linear_params) == 1.25
    assert math.isclose(decode_log(1, log_params), 10.0)
