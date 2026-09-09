"""Bounded Hypothesis coverage for configured Function Tool schemas."""

from __future__ import annotations

from copy import deepcopy
import string
from typing import Any

from hypothesis import given, settings, strategies as st
import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
    validate_function_schema,
)

_SAFE_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=12,
)
_SAFE_TOKEN = st.text(
    alphabet=string.ascii_lowercase + string.digits + "_-",
    min_size=1,
    max_size=8,
)


@settings(max_examples=40)
@given(
    name=_SAFE_TEXT,
    count=st.integers(min_value=-100, max_value=100),
    enabled=st.booleans(),
    tags=st.lists(_SAFE_TOKEN, max_size=4),
    threshold=st.floats(
        min_value=-10,
        max_value=10,
        allow_nan=False,
        allow_infinity=False,
        width=32,
    ),
    label=st.one_of(st.none(), _SAFE_TEXT),
)
def test_nested_schema_coerces_valid_values_without_mutating_input(
    name: str,
    count: int,
    enabled: bool,
    tags: list[str],
    threshold: float,
    label: str | None,
) -> None:
    """Nested required/optional fields retain predictable scalar coercion."""
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 12},
                "count": {"type": "integer", "minimum": -100, "maximum": 100},
                "settings": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 4,
                        },
                        "threshold": {
                            "type": "number",
                            "minimum": -10,
                            "maximum": 10,
                        },
                        "label": {"type": "string", "maxLength": 12},
                    },
                    "required": ["enabled", "tags", "threshold"],
                    "additionalProperties": False,
                },
            },
            "required": ["name", "count", "settings"],
            "additionalProperties": False,
        }
    }
    arguments: dict[str, Any] = {
        "name": name,
        "count": f" {count} ",
        "settings": {
            "enabled": str(enabled).lower(),
            "tags": list(tags),
            "threshold": f" {threshold} ",
        },
    }
    if label is not None:
        arguments["settings"]["label"] = label
    before = deepcopy(arguments)

    validate_function_schema(spec["parameters"])
    validated = validate_function_arguments(spec, arguments)

    expected_settings: dict[str, Any] = {
        "enabled": enabled,
        "tags": tags,
        "threshold": float(str(threshold)),
    }
    if label is not None:
        expected_settings["label"] = label
    assert validated == {
        "name": name,
        "count": count,
        "settings": expected_settings,
    }
    assert arguments == before


@st.composite
def _missing_required_case(draw):
    field_count = draw(st.integers(min_value=1, max_value=5))
    missing_index = draw(st.integers(min_value=0, max_value=field_count - 1))
    fields = [f"required_{index}" for index in range(field_count)]
    missing = fields[missing_index]
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                **{field: {"type": "string"} for field in fields},
                "optional": {"type": "string"},
            },
            "required": fields,
            "additionalProperties": False,
        }
    }
    arguments = {field: "present" for field in fields if field != missing}
    arguments["optional"] = "kept"
    return spec, arguments, missing


@settings(max_examples=30)
@given(case=_missing_required_case())
def test_generated_required_fields_fail_when_exactly_one_is_missing(
    case: tuple[dict[str, Any], dict[str, Any], str],
) -> None:
    """Optional fields never compensate for a missing generated required field."""
    spec, arguments, missing = case

    with pytest.raises(HomeAssistantError, match="Missing required function input") as err:
        validate_function_arguments(spec, arguments)

    assert missing in str(err.value)


@st.composite
def _union_case(draw):
    if draw(st.booleans()):
        value = draw(st.integers(min_value=-1000, max_value=1000))
        return str(value), value, int
    value = draw(st.booleans())
    return ("true" if value else "false"), value, bool


@settings(max_examples=40)
@given(
    case=_union_case(),
    invalid_suffix=st.text(alphabet=string.ascii_lowercase, max_size=8),
)
def test_type_union_uses_first_matching_supported_branch_and_rejects_misses(
    case: tuple[str, int | bool, type[int] | type[bool]],
    invalid_suffix: str,
) -> None:
    """The supported type-list union behaves like a bounded alternative/Any."""
    raw, expected, expected_type = case
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": ["integer", "boolean"]}},
            "required": ["value"],
            "additionalProperties": False,
        }
    }

    validated = validate_function_arguments(spec, {"value": raw})
    assert validated["value"] == expected
    assert type(validated["value"]) is expected_type

    with pytest.raises(HomeAssistantError, match="must be integer or boolean"):
        validate_function_arguments(spec, {"value": f"word_{invalid_suffix}"})


@settings(max_examples=35)
@given(items=st.lists(_SAFE_TOKEN, min_size=1, max_size=5, unique=True))
def test_array_coercion_still_applies_item_count_and_uniqueness_constraints(
    items: list[str],
) -> None:
    """Comma-list coercion cannot bypass the array's combined restrictions."""
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 6,
                    "uniqueItems": True,
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }
    }

    assert validate_function_arguments(spec, {"items": ",".join(items)}) == {
        "items": items
    }

    with pytest.raises(HomeAssistantError, match="unique items"):
        validate_function_arguments(spec, {"items": [*items, items[0]]})


@settings(max_examples=35)
@given(
    suffix=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
    value=st.integers(min_value=-1000, max_value=1000),
)
def test_additional_properties_preserve_reject_or_validate_by_declared_policy(
    suffix: str,
    value: int,
) -> None:
    """Open, closed and typed additional properties have distinct stable behavior."""
    key = f"extra_{suffix}"
    arguments = {"known": "ok", key: str(value)}
    properties = {"known": {"type": "string"}}

    open_spec = {
        "parameters": {
            "type": "object",
            "properties": properties,
            "additionalProperties": True,
        }
    }
    assert validate_function_arguments(open_spec, arguments) == arguments

    typed_spec = {
        "parameters": {
            "type": "object",
            "properties": properties,
            "additionalProperties": {"type": "integer"},
        }
    }
    assert validate_function_arguments(typed_spec, arguments) == {
        "known": "ok",
        key: value,
    }

    closed_spec = {
        "parameters": {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
    }
    with pytest.raises(HomeAssistantError, match="Unknown function input") as err:
        validate_function_arguments(closed_spec, arguments)
    assert key in str(err.value)


@st.composite
def _bounded_integer_case(draw):
    lower = draw(st.integers(min_value=-100, max_value=100))
    width = draw(st.integers(min_value=1, max_value=20))
    upper = lower + width
    value = draw(st.integers(min_value=lower, max_value=upper))
    return lower, upper, value


@settings(max_examples=40)
@given(case=_bounded_integer_case())
def test_generated_numeric_bounds_accept_inside_and_reject_both_sides(
    case: tuple[int, int, int],
) -> None:
    """Coercion occurs before all declared numeric bounds are enforced."""
    lower, upper, value = case
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "integer",
                    "minimum": lower,
                    "maximum": upper,
                }
            },
            "required": ["value"],
            "additionalProperties": False,
        }
    }

    assert validate_function_arguments(spec, {"value": str(value)}) == {"value": value}

    with pytest.raises(HomeAssistantError, match="at least"):
        validate_function_arguments(spec, {"value": str(lower - 1)})
    with pytest.raises(HomeAssistantError, match="at most"):
        validate_function_arguments(spec, {"value": str(upper + 1)})


@settings(max_examples=30)
@given(choice=_SAFE_TOKEN)
def test_enum_and_const_constraints_compose_after_type_validation(choice: str) -> None:
    """A value must satisfy every supported restriction after normalization."""
    alternate = f"{choice}!"
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "enum": [choice, alternate],
                    "const": choice,
                }
            },
            "required": ["value"],
            "additionalProperties": False,
        }
    }

    assert validate_function_arguments(spec, {"value": choice}) == {"value": choice}
    with pytest.raises(HomeAssistantError, match="required value"):
        validate_function_arguments(spec, {"value": alternate})
    with pytest.raises(HomeAssistantError, match="one of its choices"):
        validate_function_arguments(spec, {"value": f"{choice}?"})


@settings(max_examples=15)
@given(keyword=st.sampled_from(["anyOf", "allOf", "oneOf", "not", "multipleOf"]))
def test_unsupported_schema_combinators_and_constraints_fail_closed(keyword: str) -> None:
    """Do not silently advertise JSON-Schema features the local runtime ignores."""
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "type": "string",
                keyword: [],
            }
        },
    }

    with pytest.raises(HomeAssistantError, match="unsupported keyword") as err:
        validate_function_schema(schema)
    assert keyword in str(err.value)