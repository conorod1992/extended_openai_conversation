"""HA/OpenAPI exclusive-bound flags become valid provider JSON Schema bounds."""

from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("side", ["Minimum", "Maximum"])
@pytest.mark.parametrize("flag", [True, False, 0, 2.5])
def test_numeric_bounds_are_normalized_recursively(side, flag):
    inclusive = side.lower()
    exclusive = "exclusive" + side
    field = {"type": "number", inclusive: 0, exclusive: flag}
    schema = {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": deepcopy(field)}},
        "required": ["rows"],
    }
    _adjust_schema(schema, root=True)
    result = schema["properties"]["rows"]["items"]
    if flag is True:
        assert result[exclusive] == 0 and type(result[exclusive]) is int
        assert inclusive not in result
    elif flag is False:
        assert exclusive not in result and result[inclusive] == 0
    else:
        assert result[exclusive] == flag and result[inclusive] == 0
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("flag", [True, False, 0, 2.5])
def test_azure_strips_all_numeric_bound_forms(flag):
    schema = {
        "type": "number",
        "minimum": 0,
        "maximum": 10,
        "exclusiveMinimum": flag,
        "exclusiveMaximum": flag,
    }
    _adjust_schema(schema, azure=True)
    assert not (
        {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"} & schema.keys()
    )
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("side", ["Minimum", "Maximum"])
def test_true_exclusive_flag_requires_its_numeric_bound(side):
    with pytest.raises(HomeAssistantError, match="exclusive"):
        _adjust_schema({"type": "number", "exclusive" + side: True})
