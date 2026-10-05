"""Permitted input types are alternatives, independent of schema ordering."""

from itertools import permutations
import json

import pytest

from custom_components.extended_openai_conversation_responses.function_execution import (
    _validate_type,
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template


@pytest.mark.parametrize(
    "types,value",
    [
        (list(order), value)
        for allowed, values in [
            (("integer", "string"), ["00123"]),
            (("number", "string"), ["00123", "1.0"]),
            (("boolean", "string"), ["true", "false"]),
            (("array", "string"), ["living_room,kitchen"]),
            (("integer", "boolean"), [True, False, 1, 0]),
            (("integer", "number", "string"), ["00123", 1, 1.0]),
        ]
        for order in permutations(allowed)
        for value in values
    ],
)
@pytest.mark.parametrize("constraint", ["enum", "const", None])
async def test_original_value_reaches_backend_exactly(hass, types, value, constraint):
    field = {"type": types}
    if constraint:
        field[constraint] = [value] if constraint == "enum" else value
    schema = {"type": "object", "properties": {"input": field}}
    arguments = validate_function_arguments({"parameters": schema}, {"input": value})
    assert type(arguments["input"]) is type(value)
    assert arguments["input"] == value
    # Run the normalized arguments through a real backend rather than checking
    # only the validator's return. Render as JSON to avoid Jinja scalar parsing.
    rendered = await TemplateFunction().execute(
        hass,
        {
            "value_template": Template("{{ input | to_json }}", hass),
        },
        arguments,
        None,
        [],
    )
    delivered = json.loads(rendered)
    assert type(delivered) is type(value)
    assert delivered == value


@pytest.mark.parametrize(
    "expected,value,result",
    [
        ("integer", "00123", 123),
        ("number", "2.5", 2.5),
        ("boolean", "false", False),
        ("array", "living_room,kitchen", ["living_room", "kitchen"]),
    ],
)
def test_single_type_compatibility_coercion_remains(expected, value, result):
    assert _validate_type("input", value, expected) == result


async def test_single_integer_decimal_reaches_backend_as_integer(hass) -> None:
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
        "additionalProperties": False,
    }
    arguments = validate_function_arguments({"parameters": schema}, {"count": 42.0})
    rendered = await TemplateFunction().execute(
        hass,
        {"value_template": Template("{{ count | to_json }}", hass)},
        arguments,
        None,
        [],
    )
    delivered = json.loads(rendered)
    assert type(delivered) is int
    assert delivered == 42


def test_union_still_coerces_when_no_permitted_type_matches():
    assert _validate_type("input", "123", ["integer", "null"]) == 123
    assert _validate_type("input", None, ["string", "null"]) is None
    with pytest.raises(HomeAssistantError):
        _validate_type("input", True, ["integer", "number"])
