"""Focused residual branch coverage for configured Function Tool validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_execution as execution,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    _collect_pattern_checks,
    _is_legacy_delay_schema,
    _schema_contains_pattern,
    _schema_without_patterns,
    _validate_expected_type,
    _validate_number_constraints,
    _validate_type,
    async_validate_function_arguments,
    split_legacy_execution_delay,
    validate_function_arguments,
    validate_function_schema,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ([], "parameters must be an object schema"),
        ({"type": "string"}, "must describe an object"),
        (
            {"type": "object", "description": 1},
            "description at `parameters` must be a string",
        ),
        (
            {"properties": {}, "items": {}},
            "mixes object and array keywords",
        ),
        (
            {"type": "object", "properties": {"value": {"type": "invalid"}}},
            "unsupported type `invalid`",
        ),
        (
            {"type": "object", "properties": {"value": {"type": []}}},
            "non-empty list",
        ),
        (
            {"type": "object", "properties": {"value": {"type": ["string", 1]}}},
            "non-empty list",
        ),
        (
            {"type": "object", "properties": {"value": {"enum": "a"}}},
            "enum at `parameters.value` must be a list",
        ),
        ({"type": "object", "properties": []}, "properties.*must be an object"),
        (
            {"type": "object", "properties": {1: {"type": "string"}}},
            "property names.*must be strings",
        ),
        (
            {"type": "object", "properties": {"value": "invalid"}},
            "schema for `parameters.value`.*must be an object",
        ),
        (
            {"type": "object", "required": "value"},
            "required.*must be a list of property names",
        ),
        (
            {"type": "object", "required": ["value", "value"]},
            "required.*contains duplicate names",
        ),
        (
            {"type": "object", "additionalProperties": 1},
            "additionalProperties.*must be boolean or an object schema",
        ),
        (
            {"type": "object", "additionalProperties": {"type": "invalid"}},
            "unsupported type `invalid`",
        ),
        ({"type": "object", "minProperties": True}, "non-negative integer"),
        (
            {"type": "object", "minProperties": 2, "maxProperties": 1},
            "cannot exceed",
        ),
        ({"type": "object", "properties": {}, "minItems": 1}, "requires a array"),
        ({"type": "array", "items": []}, "items.*must be an object schema"),
        ({"type": "array", "uniqueItems": "yes"}, "uniqueItems.*must be boolean"),
        ({"type": "string", "pattern": 1}, "pattern.*must be a string"),
        ({"type": "string", "pattern": "["}, "invalid pattern"),
        ({"type": "number", "minimum": True}, "must be a finite number"),
        ({"type": "number", "minimum": float("nan")}, "must be a finite number"),
        (
            {"type": "number", "minimum": 2, "exclusiveMaximum": 1},
            "lower bound.*exceeds upper bound",
        ),
    ],
)
def test_schema_validation_rejects_each_malformed_contract(
    schema: object, message: str
) -> None:
    """Configuration validation fails closed for every supported keyword shape."""
    with pytest.raises(HomeAssistantError, match=message):
        validate_function_schema(schema)  # type: ignore[arg-type]


def test_schema_validation_accepts_nullable_nested_types_and_empty_array_items() -> (
    None
):
    """Valid union declarations and omitted optional array items remain accepted."""
    validate_function_schema(
        {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "items": {"type": "array", "items": {"type": "string"}},
                "count": {"type": "number", "minimum": 0},
            },
        }
    )
    with pytest.raises(HomeAssistantError, match="unsupported keyword"):
        validate_function_schema({"type": "object", "unknown": True})


def test_argument_entrypoint_rejects_bad_schema_and_non_object_result() -> None:
    """The public entrypoint keeps both defensive top-level schema checks."""
    with pytest.raises(HomeAssistantError, match="schema is invalid"):
        validate_function_arguments({"parameters": []}, {})

    with (
        patch.object(execution, "_validate_value", return_value="not-an-object"),
        pytest.raises(HomeAssistantError, match="must describe an object"),
    ):
        validate_function_arguments({"parameters": {}}, {})


def test_pattern_tree_helpers_cover_inference_and_additional_properties() -> None:
    """Pattern discovery traverses inferred objects, arrays, and open properties."""
    schema = {
        "properties": {"known": {"type": "string", "pattern": "^a"}},
        "additionalProperties": {"type": "string", "pattern": "z$"},
    }
    assert _schema_contains_pattern(schema) is True
    assert (
        _schema_contains_pattern(
            {
                "type": "object",
                "additionalProperties": {"type": "string", "pattern": "x"},
            }
        )
        is True
    )
    assert (
        _schema_contains_pattern(
            {"type": "array", "items": {"type": "string", "pattern": "x"}}
        )
        is True
    )
    assert _schema_contains_pattern({"type": "object", "properties": {}}) is False
    assert _schema_without_patterns(
        {"pattern": "root", "items": [{"pattern": "nested"}, "value"]}
    ) == {"items": [{}, "value"]}

    checks: list[tuple[str, str, str, int]] = []
    _collect_pattern_checks(
        "", {"known": "alpha", "extra": "jazz", "ignored": 1}, schema, checks
    )
    assert checks == [
        ("known", "^a", "alpha", 0),
        ("extra", "z$", "jazz", 0),
    ]

    array_checks: list[tuple[str, str, str, int]] = []
    _collect_pattern_checks(
        "",
        ["alpha", "beta"],
        {"items": {"type": "string", "pattern": "a"}},
        array_checks,
    )
    assert [check[0] for check in array_checks] == ["input[0]", "input[1]"]

    no_property_checks: list[tuple[str, str, str, int]] = []
    _collect_pattern_checks(
        "input",
        {"ignored": "value"},
        {"type": "object", "properties": []},
        no_property_checks,
    )
    assert no_property_checks == []

    with pytest.raises(HomeAssistantError, match="pattern must be a string"):
        _collect_pattern_checks("value", "text", {"type": "string", "pattern": 1}, [])

    for value, pattern_schema in (
        ("value", {}),
        ("value", {"type": "string"}),
        ({"value": "text"}, {"type": "object", "properties": {"value": 1}}),
        (["value"], {"type": "array", "items": 1}),
    ):
        empty: list[tuple[str, str, str, int]] = []
        _collect_pattern_checks("", value, pattern_schema, empty)
        assert empty == []


async def test_async_pattern_validation_handles_empty_checks_and_matches(hass) -> None:
    """Optional patterned fields can be absent, while present fields use the worker."""
    optional = {
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string", "pattern": "^ok$"}},
            "additionalProperties": False,
        }
    }
    assert await async_validate_function_arguments(hass, optional, {}) == {}

    worker = AsyncMock(return_value=[True])
    with patch(
        "custom_components.extended_openai_conversation_responses.regex_execution.async_search_configured_patterns",
        worker,
    ):
        assert await async_validate_function_arguments(
            hass, optional, {"value": "ok"}
        ) == {"value": "ok"}
    worker.assert_awaited_once()


@pytest.mark.parametrize(
    "schema",
    [
        None,
        {"type": "string"},
        {"type": "object"},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {"days": {"type": "integer"}}},
        {"type": "object", "properties": {"seconds": {"type": "string"}}},
    ],
)
def test_legacy_delay_recognition_rejects_lookalike_schemas(schema: object) -> None:
    """Only the documented numeric hours/minutes/seconds object is scheduling data."""
    assert _is_legacy_delay_schema(schema) is False


def test_legacy_delay_split_handles_malformed_parents_and_values() -> None:
    """Malformed schema containers and non-object delay values remain tool arguments."""
    arguments = {"delay": 5, "message": "hello"}
    for spec in (
        {"parameters": []},
        {"parameters": {"properties": []}},
        {
            "parameters": {
                "properties": {
                    "delay": {
                        "type": "object",
                        "properties": {"seconds": {"type": "number"}},
                    }
                }
            }
        },
    ):
        assert split_legacy_execution_delay(spec, arguments) == (arguments, None)


@pytest.mark.parametrize(
    ("expected", "value", "normalized"),
    [
        ("number", " 2.5 ", 2.5),
        ("integer", " 2 ", 2),
        ("boolean", "TRUE", True),
        ("boolean", "false", False),
        ("array", "one, , two", ["one", "two"]),
        ("object", {"value": 1}, {"value": 1}),
        ("null", None, None),
    ],
)
def test_expected_type_safe_coercions(
    expected: str, value: object, normalized: object
) -> None:
    """Document every intentional scalar and collection coercion."""
    assert _validate_expected_type("value", value, expected) == normalized


@pytest.mark.parametrize(
    ("expected", "value"),
    [
        ("string", 1),
        ("number", True),
        ("number", float("inf")),
        ("number", "not-a-number"),
        ("integer", True),
        ("integer", "2.5"),
        ("boolean", "yes"),
        ("array", 1),
        ("object", []),
        ("null", "value"),
    ],
)
def test_expected_type_rejects_invalid_runtime_values(
    expected: str, value: object
) -> None:
    """Each runtime type guard returns the same bounded input error."""
    with pytest.raises(HomeAssistantError, match="Function input `value` must be"):
        _validate_expected_type("value", value, expected)


def test_type_validation_rejects_bad_declarations_and_preserves_union_errors() -> None:
    """Runtime declarations fail closed and nullable unions report all candidates."""
    assert _validate_type("value", "unchanged", None) == "unchanged"
    with pytest.raises(HomeAssistantError, match="unsupported type"):
        _validate_type("value", "text", "invalid")
    for declaration in ([], ["string", 1], ("string", "null")):
        with pytest.raises(HomeAssistantError, match="non-empty list"):
            _validate_type("value", "text", declaration)
    with pytest.raises(HomeAssistantError, match="string or null"):
        _validate_type("value", 1, ["string", "null"])

    with (
        patch.object(
            execution,
            "_validate_expected_type",
            side_effect=HomeAssistantError("Function input schema is invalid: broken"),
        ),
        pytest.raises(HomeAssistantError, match="schema is invalid"),
    ):
        _validate_type("value", "text", ["string"])

    with pytest.raises(HomeAssistantError, match="schema is invalid"):
        _validate_expected_type("value", "text", "invalid")


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"minimum": "zero"}, "minimum must be numeric"),
        ({"minimum": 2}, "at least 2"),
        ({"maximum": 0}, "at most 0"),
        ({"exclusiveMinimum": 1}, "greater than 1"),
        ({"exclusiveMaximum": 1}, "less than 1"),
    ],
)
def test_numeric_constraint_failures(schema: dict, message: str) -> None:
    """Malformed and unsatisfied numeric bounds retain precise diagnostics."""
    with pytest.raises(HomeAssistantError, match=message):
        _validate_number_constraints("value", 1, schema)


@pytest.mark.parametrize(
    ("schema", "arguments", "message"),
    [
        (
            {"type": "object", "minProperties": -1},
            {},
            "non-negative integer",
        ),
        (
            {"type": "object", "maxProperties": 0},
            {"value": 1},
            "at most 0 properties",
        ),
        (
            {"type": "object", "properties": []},
            {},
            "properties must be an object",
        ),
        (
            {"type": "object", "required": "value"},
            {},
            "required must be a list",
        ),
        (
            {"type": "object", "additionalProperties": 1},
            {},
            "additionalProperties must be boolean",
        ),
        (
            {"type": "object", "additionalProperties": True},
            {1: "value"},
            "object keys must be strings",
        ),
        (
            {"type": "object", "properties": {"value": "invalid"}},
            {"value": 1},
            "schema for `value` must be an object",
        ),
        (
            {"type": "array", "uniqueItems": "yes"},
            [],
            "uniqueItems must be boolean",
        ),
        (
            {"type": "array", "items": "invalid"},
            [],
            "items for `input` must be an object schema",
        ),
        (
            {"type": "string", "pattern": 1},
            "value",
            "pattern must be a string",
        ),
        (
            {"type": "string", "pattern": "["},
            "value",
            "invalid pattern",
        ),
        (
            {"type": "string", "enum": "value"},
            "value",
            "enum must be a list",
        ),
    ],
)
def test_runtime_constraints_reject_malformed_or_out_of_bounds_values(
    schema: dict, arguments: object, message: str
) -> None:
    """Residual object, array, string, and enum error paths are enforced."""
    spec = {"parameters": schema}
    if schema.get("type") != "object":
        with pytest.raises(HomeAssistantError, match=message):
            execution._validate_value("", arguments, schema)
        return
    with pytest.raises(HomeAssistantError, match=message):
        validate_function_arguments(spec, arguments)  # type: ignore[arg-type]


def test_array_inference_and_optional_items_are_preserved() -> None:
    """Legacy array hints infer their type and do not require an items schema."""
    assert execution._validate_value("", [1, 2], {"minItems": 1}) == [1, 2]
    assert execution._validate_value("", [], {"type": "array"}) == []
    assert execution._validate_value("", "unchanged", {}) == "unchanged"


def test_runtime_nested_additional_unique_and_const_constraints() -> None:
    """Cover successful open-property validation and the remaining value failures."""
    schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": {"type": "integer"},
    }
    assert validate_function_arguments({"parameters": schema}, {"dynamic": "2"}) == {
        "dynamic": 2
    }
    assert execution._validate_value(
        "values", ["first", "second"], {"type": "array", "uniqueItems": True}
    ) == ["first", "second"]

    with pytest.raises(HomeAssistantError, match="unique items"):
        execution._validate_value(
            "values", ["same", "same"], {"type": "array", "uniqueItems": True}
        )
    with pytest.raises(HomeAssistantError, match="required value"):
        execution._validate_value("value", "actual", {"const": "expected"})
