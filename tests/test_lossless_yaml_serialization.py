"""The shared emitter preserves data without making ordinary Unicode unreadable."""

from datetime import date
import json

from hypothesis import given, settings, strategies as st
import pytest
import yaml

from custom_components.extended_openai_conversation_responses.yaml_serialization import (
    dump_yaml,
    load_yaml_or_json,
)


def test_function_documents_keep_their_format_scalar_semantics():
    document = {"number": 0.000001, "large": 1e20, "literal": "1e-06"}
    assert load_yaml_or_json(json.dumps(document)) == document
    assert load_yaml_or_json(dump_yaml(document)) == document
    # Authored YAML retains PyYAML's safe scalar/tag vocabulary.
    assert load_yaml_or_json("value: 1e-06\ndate: 2026-10-17") == {
        "value": "1e-06",
        "date": date(2026, 10, 17),
    }
    with pytest.raises(yaml.YAMLError):
        load_yaml_or_json("!!python/object/apply:os.system ['false']")


@given(st.text(alphabet=st.characters(codec="utf-8"), max_size=256))
@settings(max_examples=100, deadline=None)
def test_generated_scalar_keys_and_values_round_trip_exactly(text):
    value = {text: [text, {1: text}]}
    assert yaml.safe_load(dump_yaml(value)) == value


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029", "\r\n", "\0"])
def test_sensitive_scalar_types_and_literal_markers_are_preserved(separator):
    secret = "FABRICATED-before" + separator + "[redacted]after"
    value = {
        "password": secret,
        secret: {date(2026, 10, 23): secret},
        "typed": [b"fabricated\x00\xff", 2**128, {"one", "two"}],
        "literal": {"__extended_openai_redacted_secret__": True},
    }
    assert yaml.safe_load(dump_yaml(value)) == value


@pytest.mark.parametrize("sort_keys", [False, True])
def test_ordinary_yaml_remains_readable_and_byte_compatible(sort_keys):
    value = {"description": "Café 日本語 😀", "variables": {"units": "c"}}
    assert dump_yaml(value, sort_keys=sort_keys) == yaml.safe_dump(
        value, sort_keys=sort_keys, allow_unicode=True
    )
    assert "Café 日本語 😀" in dump_yaml(value)


def test_safe_dumper_is_local_and_unsafe_python_tags_stay_rejected():
    source = yaml.safe_dump("before\x85after", allow_unicode=True)
    assert yaml.safe_load(source) == "before after"  # Global behavior is untouched.
    assert yaml.safe_dump("before\x85after", allow_unicode=True) == source
    with pytest.raises(yaml.representer.RepresenterError):
        dump_yaml(object())
