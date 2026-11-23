"""Provider evidence survives schema analysis without changing private data."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_references import tool_with_schema

SECRET = "fabricated-weather-credential-42"


def weather_tool(annotation, shape, host="api.openweathermap.org"):
    data = {"appid": SECRET}
    instance = [data] if annotation in {"examples", "enum"} else data
    leaf = {"type": "object", annotation: instance}
    if shape == "instance":
        schema = {"type": "object", "properties": {"value": leaf}}
    elif shape == "nested":
        schema = {
            "type": "object",
            "properties": {"values": {"type": "array", "items": leaf}},
        }
    elif shape == "composed":
        schema = {"allOf": [{"type": "object", "properties": {"value": leaf}}]}
    elif shape == "reference":
        schema = {
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/value"}},
            "$defs": {"value": leaf},
        }
    elif shape == "sensitive_reference":
        schema = {
            "type": "object",
            "properties": {"appid": {"$ref": "#/$defs/value"}},
            "$defs": {
                "value": {
                    annotation: [SECRET]
                    if annotation in {"examples", "enum"}
                    else SECRET
                }
            },
        }
    else:
        schema = {
            "type": "object",
            "patternProperties": {
                "^appid$": {
                    annotation: [SECRET]
                    if annotation in {"examples", "enum"}
                    else SECRET
                }
            },
        }
    tool = tool_with_schema(schema)
    tool["function"]["resource"] = f"https://{host}/data/2.5/weather"
    return tool


@pytest.mark.parametrize("destination", ["resource", "resource_template"])
@pytest.mark.parametrize("composite", [False, True])
def test_private_weather_configuration_is_exactly_preserved(
    hass, destination, composite
):
    document = private_document()
    tool = weather_tool("default", "reference")
    if destination == "resource_template":
        tool["function"][destination] = tool["function"].pop("resource")
    if composite:
        tool["function"] = {"type": "composite", "sequence": tool["function"]}
    document["agent"]["config"]["functions"] = yaml.safe_dump([tool])
    before = deepcopy(document)
    result = backup.finalize_backup_snapshot(document)
    assert decode_document(json.loads(result["json"])) == before
    assert document == before
