"""The shared emitter preserves data without making ordinary Unicode unreadable."""

from dataclasses import replace
from datetime import date
import json
import math

from hypothesis import given, settings, strategies as st
import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    yaml_serialization,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    authored_function_configuration_equal,
    dump_authored_function_configuration,
)
from custom_components.extended_openai_conversation_responses.yaml_serialization import (
    InvalidSerializedDocument,
    InvalidSerializedUnicode,
    SerializedDocumentLimit,
    YamlLimits,
    dump_yaml,
    load_authored_config,
    load_unicode_yaml,
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


@pytest.mark.parametrize("load", [load_authored_config, load_yaml_or_json])
def test_nonstandard_json_constants_use_safe_yaml_scalar_semantics(load):
    expected = ["NaN", "Infinity", "-Infinity"]
    for value in expected:
        assert load(value) == value
    literals = load('{"value": [NaN, Infinity, -Infinity]}')
    assert literals == {"value": expected}
    assert authored_function_configuration_equal(
        literals, load(dump_authored_function_configuration(literals))
    )
    assert load('{"value": 1e-06, "text": "Infinity"}') == {
        "value": 1e-6,
        "text": "Infinity",
    }
    nan, positive, negative = load("[.nan, .inf, -.inf]")
    assert math.isnan(nan)
    assert positive == math.inf
    assert negative == -math.inf


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


@pytest.mark.parametrize("tag", ["omap", "pairs"])
def test_authored_identity_preserves_yaml_pair_types_and_order(tag):
    value = load_yaml_or_json(f"lookup: !!{tag} [{{z: 1}}, {{a: 2}}]")
    assert (
        load_yaml_or_json(dump_yaml(value)) == value == {"lookup": [("z", 1), ("a", 2)]}
    )
    assert authored_function_configuration_equal(
        value, load_yaml_or_json(dump_authored_function_configuration(value))
    )
    assert not authored_function_configuration_equal(
        value, {"lookup": [["z", 1], ["a", 2]]}
    )
    assert not authored_function_configuration_equal(
        value, {"lookup": [("a", 2), ("z", 1)]}
    )


def test_authored_yaml_preserves_pair_aliases_and_unhashable_keys():
    value = load_yaml_or_json(
        "lookup: &pairs !!pairs\n  - ? [one, two]\n    : *pairs\n"
    )
    decoded = load_yaml_or_json(dump_authored_function_configuration(value))
    [(key, alias)] = decoded["lookup"]
    assert key == ["one", "two"]
    assert alias is decoded["lookup"]


def test_authored_identity_keeps_recursive_yaml_collections_reversible():
    value = load_yaml_or_json("&self [*self]")
    decoded = load_yaml_or_json(dump_authored_function_configuration(value))
    assert decoded[0] is decoded


def test_configuration_only_accepts_acyclic_aliases():
    source = """
base: &base {shared: first, own: base}
other: &other {shared: second, another: extra}
actual: {<<: [*base, *other], own: explicit}
alias: *base
"""
    decoded = load_authored_config(source)
    assert decoded["actual"] == {
        "shared": "first",
        "another": "extra",
        "own": "explicit",
    }
    assert decoded["alias"] is decoded["base"]
    with pytest.raises(InvalidSerializedDocument, match="Recursive YAML aliases"):
        load_authored_config("&self [*self]")


def test_small_yaml_expansion_budget_is_enforced_before_construction(monkeypatch):
    def unexpected_construction(*_args):
        pytest.fail("Construction must happen after alias expansion is bounded")

    monkeypatch.setattr(yaml.SafeLoader, "construct_document", unexpected_construction)
    limits = YamlLimits(max_expanded_nodes=14)
    with pytest.raises(SerializedDocumentLimit, match="alias expansion"):
        load_unicode_yaml(
            "names: &names [one, two, three]\nreferences: [*names, *names]",
            limits=limits,
        )
    with pytest.raises(SerializedDocumentLimit, match="merge expansion"):
        load_unicode_yaml(
            "<<: {<<: {<<: {<<: {a: 1, b: 2, c: 3, d: 4}}}}",
            limits=replace(limits, max_expanded_nodes=18),
        )


def test_yaml_alias_height_is_checked_at_each_reference():
    with pytest.raises(SerializedDocumentLimit, match="nesting limit"):
        load_unicode_yaml(
            "base: &base [first, [second, third]]\nuse: [[[*base]]]",
            limits=YamlLimits(max_depth=6),
        )


def test_yaml_rejects_over_budget_scalars_with_their_specific_limits():
    with pytest.raises(SerializedDocumentLimit, match="numeric scalar"):
        load_unicode_yaml("amount: !!int " + "7" * 16_385)
    with pytest.raises(SerializedDocumentLimit, match="scalar exceeds the expansion"):
        load_unicode_yaml(
            "label: " + "a" * 20, limits=YamlLimits(max_expanded_chars=12)
        )


def test_yaml_preserves_aliases_but_rejects_invalid_merge_sources():
    with pytest.raises(InvalidSerializedDocument, match="Recursive YAML merges"):
        load_yaml_or_json("recursive: &recursive {<<: *recursive}")
    with pytest.raises(InvalidSerializedDocument, match="merge requires a mapping"):
        load_authored_config("invalid: {<<: [text]}")


def test_json_fast_path_checks_source_size_depth_and_nodes(monkeypatch):
    monkeypatch.setattr(
        yaml_serialization,
        "_AUTHORED_LIMITS",
        YamlLimits(max_source_bytes=64, max_nodes=6, max_depth=3),
    )
    assert load_authored_config('{"n": [1e-06]}') == {"n": [1e-6]}
    assert load_authored_config("['[[]] [[]]']") == ["[[]] [[]]"]
    with pytest.raises(SerializedDocumentLimit, match="node limit"):
        load_authored_config("[1, 2, 3, 4, 5, 6]")
    with pytest.raises(SerializedDocumentLimit, match="nesting limit"):
        load_authored_config("[[[1]]]")
    with pytest.raises(SerializedDocumentLimit, match="text size"):
        load_authored_config(json.dumps("😀" * 20))


@pytest.mark.parametrize(
    "source",
    ["date: 2026-02-29", "d: !!timestamp invalid", "n: !!int ''", "b: !!bool invalid"],
)
def test_malformed_safe_tags_report_an_authored_document_error(source):
    with pytest.raises(InvalidSerializedDocument, match="Invalid YAML scalar"):
        load_authored_config(source)


@pytest.mark.parametrize("escape", [r"\uD83D", r"\U00110000"])
def test_guarded_yaml_keeps_the_specific_bad_unicode_error(escape):
    assert load_unicode_yaml(r'emoji: "\uD83D\uDE00"') == {"emoji": "😀"}
    with pytest.raises(InvalidSerializedUnicode):
        load_unicode_yaml(f'emoji: "{escape}"')


def test_function_yaml_writer_uses_the_same_limit_as_the_reader(monkeypatch):
    monkeypatch.setattr(
        yaml_serialization, "_AUTHORED_LIMITS", YamlLimits(max_source_bytes=4)
    )
    assert agent_config._tools_yaml([]) == "[]\n"
    with pytest.raises(agent_config.AgentConfigError, match="text size"):
        agent_config.require_persistable_function_yaml("test: valid\n")


def test_large_function_documents_share_only_one_volatile_parse_slot():
    agent_config._decoded_function_tools.cache_clear()
    agent_config._last_large_function_document.cache_clear()
    try:
        for prefix in ("first", "second", "third"):
            text = prefix + "a" * (32 * 1024)
            loaded = agent_config.parse_configured_function_tools(
                {agent_config.CONF_FUNCTION_TOOLS: json.dumps([text])}
            )
            assert loaded == [text]
        assert agent_config._last_large_function_document.cache_info().currsize == 1
        assert agent_config._decoded_function_tools.cache_info().currsize == 0
    finally:
        agent_config._last_large_function_document.cache_clear()
