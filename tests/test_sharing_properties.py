"""Structural guarantees are independent of any generated credential alphabet."""


from hypothesis import given, settings, strategies as st
import pytest

from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_field,
    encode_field,
)


@settings(max_examples=75, deadline=None)
@given(
    st.one_of(
        st.text(),
        st.binary(),
        st.dictionaries(st.text(), st.one_of(st.text(), st.binary()), max_size=5),
    )
)
def test_private_codec_preserves_arbitrary_fabricated_values(value):
    assert decode_field(encode_field(value)) == value


@pytest.mark.parametrize(
    "encoded",
    [
        {},
        {"encoding": "unknown", "content": "x"},
        {"encoding": "yaml-safe-v1", "content": 1},
    ],
)
def test_private_codec_rejects_unknown_or_malformed_envelopes(encoded):
    with pytest.raises(ValueError):
        decode_field(encoded)
