"""Structural guarantees are independent of any generated credential alphabet."""

import json
from pathlib import Path

from hypothesis import given, settings, strategies as st
import pytest

from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_field,
    encode_field,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SECURITY_CONTRACT,
    project_configuration,
)


@settings(max_examples=100, deadline=None)
@given(st.text(), st.binary())
def test_unclassified_values_do_not_influence_restricted_projection(text, binary):
    source = {
        "future_field": {text: binary},
        "prompt": text,
        "functions": [
            {
                "spec": {"name": text},
                "function": {"type": "custom-future", "content": binary},
            }
        ],
        "temperature": 0.5,
    }
    expected = {
        "future_field": {"other": "other"},
        "prompt": "other",
        "functions": [
            {
                "spec": {"name": "other"},
                "function": {"type": "custom-future", "content": "other"},
            }
        ],
        "temperature": 0.5,
    }
    assert project_configuration(source) == project_configuration(expected)


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


def test_security_contract_is_identical_in_backend_and_interface():
    path = (
        Path(__file__).parents[1]
        / "custom_components/extended_openai_conversation_responses/frontend/sharing-review.js"
    )
    first_line = path.read_text(encoding="utf8").splitlines()[0]
    assert (
        json.loads(first_line.split(" = ", 1)[1].removesuffix(";")) == SECURITY_CONTRACT
    )


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
