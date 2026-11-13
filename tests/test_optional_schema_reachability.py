"""Only reachable optional schemas opt out of Responses normalization."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)
from tests.test_responses_optional_function_tools import tool


def schema_with_library():
    return {
        "type": "object",
        "properties": {"value": {"type": ["string", "null"]}},
        "required": ["value"],
        "$defs": {
            "unused": {"type": "object", "properties": {"optional": {"type": "string"}}}
        },
    }


@pytest.mark.parametrize("library", ["$defs", "definitions"])
@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_unreferenced_optional_library_does_not_change_effective_policy(library, mode):
    schema = schema_with_library()
    if library != "$defs":
        schema[library] = schema.pop("$defs")
    source = tool(schema)
    before = deepcopy(source)
    entry = format_function_tools([source], mode)[0]
    assert "strict" not in (entry if mode == "responses" else entry["function"])
    assert source == before


@pytest.mark.parametrize(
    "name,reference",
    [
        ("optional", "#/$defs/optional"),
        ("a/b~c", "#/$defs/a~1b~0c"),
        ("space name", "#/$defs/space%20name"),
    ],
)
def test_referenced_optional_library_is_followed(name, reference):
    schema = schema_with_library()
    schema["$defs"][name] = schema["$defs"].pop("unused")
    schema["properties"]["value"] = {"$ref": reference}
    assert format_function_tools([tool(schema)], "responses")[0]["strict"] is False


def test_cycles_are_bounded_and_required_nullable_contract_stays_exact():
    schema = schema_with_library()
    schema["$defs"]["cycle"] = {"$ref": "#/$defs/cycle"}
    schema["properties"]["value"]["allOf"] = [{"$ref": "#/$defs/cycle"}]
    entry = format_function_tools([tool(schema)], "responses")[0]
    assert "strict" not in entry
    assert entry["parameters"] == schema


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/schema",
        "#anchor",
        "#/$defs/missing",
        "#/$defs/%zz",
        "#/$defs/bad~2name",
        "#/$defs/%ff",
        "#/allOf/-1",
        "#/allOf/00",
    ],
)
def test_unknown_reference_scope_keeps_conservative_non_strict_fallback(reference):
    schema = schema_with_library()
    schema["allOf"] = [{"type": "string"}]
    schema["properties"]["value"] = {"$ref": reference}
    assert format_function_tools([tool(schema)], "responses")[0]["strict"] is False


@pytest.mark.parametrize("strict", [True, False])
def test_explicit_policy_always_wins(strict):
    source = tool(schema_with_library())
    source["spec"]["strict"] = strict
    assert format_function_tools([source], "responses")[0]["strict"] is strict


@pytest.mark.parametrize(
    "child",
    [{"$id": "nested"}, {"$dynamicRef": "#node"}, {"$recursiveRef": "#"}],
)
def test_unsupported_resource_scopes_do_not_guess_strictness(child):
    schema = {"allOf": [child]}
    assert format_function_tools([tool(schema)], "responses")[0]["strict"] is False


@pytest.mark.parametrize("target", [True, False, {"type": "string"}])
def test_array_pointer_targets_keep_required_schema_strictness(target):
    schema = {"allOf": [target], "$ref": "#/allOf/0"}
    assert "strict" not in format_function_tools([tool(schema)], "responses")[0]


@pytest.mark.parametrize(
    "schema",
    [
        {"allOf": [{"type": "string"} for _ in range(513)]},
        {"$ref": "#" + "/nested" * 33},
        {"$defs": {"data": "not a schema"}, "$ref": "#/$defs/data"},
    ],
)
def test_traversal_limits_and_non_schema_targets_are_conservative(schema):
    assert format_function_tools([tool(schema)], "responses")[0]["strict"] is False
