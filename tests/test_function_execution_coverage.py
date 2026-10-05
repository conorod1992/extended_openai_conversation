"""Focused residual branch coverage for configured Function Tool validation."""

from __future__ import annotations

from copy import deepcopy
import re
from unittest.mock import AsyncMock, patch

from hypothesis import given, settings, strategies as st
from jsonschema import Draft202012Validator
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
    "items_schema,raw,valid",
    [
        ({"type": "integer"}, ["01", 1], False),
        ({"type": "boolean"}, ["false", False], False),
        (
            {"type": "object", "properties": {"n": {"type": "integer"}}},
            [{"n": "01"}, {"n": 1}],
            False,
        ),
        ({}, [0, False], True),
        ({}, [1, True], True),
        ({}, [{"n": 0}, {"n": False}], True),
        ({}, [[0], [False]], True),
        ({}, [1, 1.0], False),
        ({"type": "integer"}, ["01", "2"], True),
        ({"type": "boolean"}, ["false", "true"], True),
        ({"type": "integer"}, [1, 1], False),
        ({}, [{"a": 1, "b": 2}, {"b": 2, "a": 1}], False),
        ({}, [[1, 2], [2, 1]], True),
        ({}, [{"a": [1, False]}, {"a": [1.0, False]}], False),
        ({}, [-0.0, 0], False),
        ({}, [2**53 + 1, float(2**53)], True),
        ({}, [None, "null", "1", 1], True),
    ],
    ids=[
        "integer-coercion",
        "boolean-coercion",
        "object-coercion",
        "false-zero",
        "true-one",
        "object-bool-number",
        "array-bool-number",
        "numeric-equivalence",
        "healthy-integers",
        "healthy-booleans",
        "direct-duplicate",
        "object-key-order",
        "array-order",
        "nested-numeric-equivalence",
        "signed-zero",
        "large-integer-precision",
        "scalar-types",
    ],
)
def test_unique_items_checks_normalized_json_values(items_schema, raw, valid):
    schema = {"type": "array", "items": items_schema, "uniqueItems": True}
    spec = {"parameters": {"type": "object", "properties": {"values": schema}}}
    before = deepcopy(raw)
    if valid:
        normalized = validate_function_arguments(spec, {"values": raw})
        Draft202012Validator(spec["parameters"]).validate(normalized)
    else:
        with pytest.raises(HomeAssistantError, match="unique items"):
            validate_function_arguments(spec, {"values": raw})
    assert raw == before


@pytest.mark.parametrize("size", [512, 2048])
@pytest.mark.parametrize("kind", ["numbers", "objects", "arrays"])
@pytest.mark.parametrize("late_duplicate", [False, True])
def test_large_unique_arrays_bound_validation_work(
    monkeypatch, size, kind, late_duplicate
):
    """Distinct and late-duplicate inputs must avoid pairwise work as they grow."""
    values = [
        {"entity_id": f"light.item_{index}", "enabled": True}
        if kind == "objects"
        else [index, False]
        if kind == "arrays"
        else index
        for index in range(size)
    ]
    if late_duplicate:
        values.append(deepcopy(values[0]))
    hashes = comparisons = 0
    original_hash, original_equal = execution._json_hash, execution._json_equal

    def counted_hash(value):
        nonlocal hashes
        hashes += 1
        return original_hash(value)

    def counted_equal(left, right):
        nonlocal comparisons
        comparisons += 1
        return original_equal(left, right)

    monkeypatch.setattr(execution, "_json_hash", counted_hash)
    monkeypatch.setattr(execution, "_json_equal", counted_equal)
    spec = {
        "parameters": {"properties": {"values": {"type": "array", "uniqueItems": True}}}
    }
    if late_duplicate:
        with pytest.raises(HomeAssistantError, match="unique items"):
            validate_function_arguments(spec, {"values": values})
    else:
        assert validate_function_arguments(spec, {"values": values}) == {
            "values": values
        }
    assert hashes <= 3 * len(values)
    assert comparisons <= 4 * len(values)


def test_unique_items_resolves_hash_collisions_with_exact_json_equality(monkeypatch):
    """A hash collision must neither reject distinct values nor hide duplicates."""
    monkeypatch.setattr(execution, "_json_hash", lambda _: 0)
    schema = {"type": "array", "uniqueItems": True}
    values = [True, 1, False, 0, {"a": [1, 2]}, {"a": [2, 1]}]
    assert execution._validate_value("values", values, schema) == values
    with pytest.raises(HomeAssistantError, match="unique items"):
        execution._validate_value("values", [*values, {"a": [1.0, 2]}], schema)


def test_unique_items_preserves_untyped_python_value_equality():
    schema = {"type": "array", "uniqueItems": True}
    values = [{1, 2}, {2, 3}, None]
    assert execution._validate_value("values", values, schema) == values
    with pytest.raises(HomeAssistantError, match="unique items"):
        execution._validate_value("values", [*values, {2, 1}], schema)


@pytest.mark.parametrize("keyword", ["enum", "const"])
@pytest.mark.parametrize(
    "value,literal,valid",
    [
        (False, 0, False),
        (True, 1, False),
        (0, False, False),
        ({"n": False}, {"n": 0}, False),
        ([True], [1], False),
        (1, 1.0, True),
        ({"a": [1], "b": 2}, {"b": 2.0, "a": [1.0]}, True),
        (False, False, True),
    ],
    ids=[
        "false-zero",
        "true-one",
        "zero-false",
        "object-bool-number",
        "array-bool-number",
        "numeric-equivalence",
        "recursive-numeric-equivalence",
        "healthy-boolean",
    ],
)
def test_enum_and_const_use_json_equality(keyword, value, literal, valid):
    schema = {keyword: [literal] if keyword == "enum" else literal}
    spec = {"parameters": {"type": "object", "properties": {"value": schema}}}
    if valid:
        normalized = validate_function_arguments(spec, {"value": value})
        Draft202012Validator(spec["parameters"]).validate(normalized)
    else:
        with pytest.raises(HomeAssistantError, match=r"choices|required value"):
            validate_function_arguments(spec, {"value": value})


@settings(max_examples=80, derandomize=True)
@given(
    values=st.lists(
        st.one_of(
            st.integers(-3, 3),
            st.booleans(),
            st.sampled_from(["01", "1", "false", "true"]),
        ),
        max_size=8,
    ),
    nested=st.booleans(),
)
def test_successful_normalization_satisfies_reference_schema(values, nested):
    """An independent validator checks the contract after intentional coercion."""
    scalar = {"type": ["integer", "boolean"]}
    items = (
        {
            "type": "object",
            "properties": {"value": scalar},
            "required": ["value"],
            "additionalProperties": False,
        }
        if nested
        else scalar
    )
    schema = {
        "type": "object",
        "properties": {
            "values": {"type": "array", "items": items, "uniqueItems": True}
        },
        "required": ["values"],
        "additionalProperties": False,
    }
    raw = {"values": [{"value": value} for value in values] if nested else values}
    expected_values = [
        int(value)
        if isinstance(value, str) and value in {"01", "1"}
        else value == "true"
        if isinstance(value, str)
        else value
        for value in values
    ]
    expected = {
        "values": [{"value": value} for value in expected_values]
        if nested
        else expected_values
    }
    reference = Draft202012Validator(schema)
    if reference.is_valid(expected):
        normalized = validate_function_arguments({"parameters": schema}, raw)
        reference.validate(normalized)
        assert normalized == expected
    else:
        with pytest.raises(HomeAssistantError, match="unique items"):
            validate_function_arguments({"parameters": schema}, raw)


def test_pattern_transform_preserves_property_names_and_literal_annotations():
    literal = {"pattern": "keep", "nested": {"pattern": "also keep"}}
    schema = {
        "properties": {"pattern": {"type": "string", "pattern": "^ok$"}},
        "items": {"properties": {"pattern": {"pattern": "nested"}}},
        "additionalProperties": {"pattern": "extra"},
        **{
            name: deepcopy(value)
            for name, value in {
                "const": literal,
                "enum": [literal],
                "default": literal,
                "example": literal,
                "examples": [literal],
                "contentSchema": literal,
            }.items()
        },
    }
    before = deepcopy(schema)
    stripped = _schema_without_patterns(schema)
    assert stripped["properties"]["pattern"] == {"type": "string"}
    assert stripped["items"]["properties"]["pattern"] == {}
    assert stripped["additionalProperties"] == {}
    for name in ("const", "enum", "default", "example", "examples", "contentSchema"):
        assert stripped[name] == schema[name]
    stripped["default"]["pattern"] = "changed copy"
    assert schema == before
    assert _schema_without_patterns([literal]) == [literal]


@pytest.mark.parametrize(
    "field", ["pattern", "type", "properties", "items", "enum", "const"]
)
async def test_keyword_named_arguments_have_equal_sync_async_validation(hass, field):
    # Execution mechanics are covered by the native wire journey; these bounded,
    # tiny regexes let this unit matrix compare validation and normalization.
    async def short_checks(_hass, checks):
        return [
            re.search(pattern, value, flags) is not None
            for pattern, value, flags in checks
        ]

    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                field: {"type": "string"},
                "label": {"type": "string", "pattern": "^ok$"},
                "count": {"type": "integer"},
                "enabled": {"type": "boolean"},
                "nullable": {"type": ["string", "null"]},
                "nested": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "pattern": "^ok$"},
                        },
                        "required": ["pattern"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [field, "label"],
            "additionalProperties": False,
        }
    }
    validate_function_schema(spec["parameters"])
    arguments = {
        field: "business data",
        "label": "ok",
        "count": "0",
        "enabled": False,
        "nullable": None,
        "nested": [{"pattern": "ok"}],
    }
    expected = {**arguments, "count": 0}
    with patch(
        "custom_components.extended_openai_conversation_responses.regex_execution.async_search_configured_patterns",
        short_checks,
    ):
        assert validate_function_arguments(spec, arguments) == expected
        assert (
            await async_validate_function_arguments(hass, spec, arguments) == expected
        )
        for invalid in (
            {**arguments, "label": "bad"},
            {**arguments, "nested": [{"pattern": "bad"}]},
            {**arguments, field: None},
        ):
            with pytest.raises(HomeAssistantError):
                validate_function_arguments(spec, invalid)
            with pytest.raises(HomeAssistantError):
                await async_validate_function_arguments(hass, spec, invalid)


@pytest.mark.parametrize("constraint", ["const", "enum"])
async def test_literal_pattern_keys_survive_async_constraints(hass, constraint):
    literal = {"pattern": "keep", "nested": {"pattern": "preserve"}}
    payload_schema = {
        "type": "object",
        constraint: literal if constraint == "const" else [literal],
    }
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "pattern": "^ok$"},
                "payload": payload_schema,
            },
            "required": ["label", "payload"],
            "additionalProperties": False,
        }
    }
    validate_function_schema(spec["parameters"])
    with patch(
        "custom_components.extended_openai_conversation_responses.regex_execution.async_search_configured_patterns",
        AsyncMock(return_value=[True]),
    ):
        args = {"label": "ok", "payload": literal}
        assert (
            await async_validate_function_arguments(hass, spec, args)
            == validate_function_arguments(spec, args)
            == args
        )
        for invalid in (
            {"label": "ok", "payload": {}},
            {"label": "ok", "payload": {"pattern": "wrong"}},
        ):
            with pytest.raises(HomeAssistantError):
                await async_validate_function_arguments(hass, spec, invalid)
            with pytest.raises(HomeAssistantError):
                validate_function_arguments(spec, invalid)


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
    """Valid unions and unknown vocabulary remain accepted with bounded warnings."""
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
    with patch.object(execution, "_LOGGER") as logger:
        warnings = validate_function_schema({"type": "object", "unknown": True})
    assert any("unknown" in warning for warning in warnings)
    logger.warning.assert_called_once()
    assert "Extended OpenAI > Functions" in logger.warning.call_args.args[0]


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
        {"pattern": "root", "items": {"pattern": "nested"}}
    ) == {"items": {}}

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, 1),
        (1.0, 1),
        (1e0, 1),
        (42.0, 42),
    ],
)
def test_single_integer_type_accepts_json_integer_values(raw, expected) -> None:
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
            "additionalProperties": False,
        }
    }
    result = validate_function_arguments(spec, {"count": raw})
    assert result == {"count": expected}
    assert type(result["count"]) is int


@pytest.mark.parametrize("raw", [1.5, True, float("inf"), float("nan")])
def test_single_integer_type_rejects_non_integer_json_values(raw) -> None:
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
    }
    with pytest.raises(HomeAssistantError, match="must be integer"):
        validate_function_arguments(spec, {"count": raw})


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
        _validate_type("value", "123", ["integer", "null"])

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
