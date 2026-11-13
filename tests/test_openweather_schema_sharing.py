"""Provider evidence survives schema analysis without changing private data."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    decode_document,
)
from tests.test_private_backup_fidelity import private_document
from tests.test_sharing_schema_context import configuration_artifact
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
@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize(
    "annotation", ["default", "example", "examples", "enum", "const"]
)
@pytest.mark.parametrize(
    "shape",
    ["instance", "nested", "composed", "reference", "sensitive_reference", "pattern"],
)
def test_provider_schema_credentials_omit_whole_tool(
    hass, format, persisted, annotation, shape, destination
):
    tool = weather_tool(annotation, shape)
    if destination == "resource_template":
        tool["function"][destination] = tool["function"].pop("resource")
    config = {"functions": yaml.safe_dump([tool]) if persisted else [tool]}
    before = deepcopy(config)
    artifact, public, serialized = configuration_artifact(config, format)
    assert SECRET not in serialized
    assert public["functions"] == []
    assert {
        "field": "function_tool_1",
        "reason": "requires_private_configuration",
        "count": 1,
    } in artifact["security"]["omitted"]
    assert config == before


@pytest.mark.parametrize(
    "host",
    ["public.example", "api.openweathermap.org.public.example", "openweathermap.org"],
)
@pytest.mark.parametrize(
    "shape", ["instance", "reference", "sensitive_reference", "pattern"]
)
def test_unrelated_provider_appids_remain_public(hass, host, shape):
    tool = weather_tool("default", shape, host)
    _, public, _ = configuration_artifact({"functions": [tool]}, "setup")
    assert public["functions"] == [tool]


@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_structural_appid_declarations_and_neighboring_tools_survive(hass, format):
    safe = weather_tool("default", "instance", "public.example")
    typed = tool_with_schema(
        {"type": "object", "properties": {"appid": {"type": "string"}}}
    )
    typed["function"]["resource"] = "https://pro.openweathermap.org/data/2.5/weather"
    unsafe = weather_tool("default", "instance")
    unsafe["spec"]["name"] = "unsafe_weather"
    safe["spec"]["name"] = "public_app"
    _, public, _ = configuration_artifact({"functions": [unsafe, safe, typed]}, format)
    assert public["functions"] == [safe, typed]
    ordinary = management_ui._export_agent(
        __import__("types").SimpleNamespace(
            title="Test", data={"functions": [unsafe, safe]}
        )
    )
    assert ordinary["config"]["functions"] == []
    assert SECRET not in json.dumps(ordinary)


def test_composite_provider_evidence_reaches_schema(hass):
    tool = weather_tool("default", "reference")
    tool["function"] = {"type": "composite", "sequence": [tool["function"]]}
    _, public, _ = configuration_artifact({"functions": [tool]}, "setup")
    assert public["functions"] == []


@pytest.mark.parametrize("format", ["setup", "configuration"])
@pytest.mark.parametrize("destination", ["resource", "resource_template"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("host", ["api.openweathermap.org", "public.example"])
def test_singleton_composite_sequence_keeps_provider_evidence(
    hass, format, destination, nested, host
):
    tool = weather_tool("default", "instance", host)
    node = tool["function"]
    if destination == "resource_template":
        node[destination] = node.pop("resource")
    composite = {"type": "composite", "sequence": node}
    tool["function"] = (
        {"type": "composite", "sequence": [composite]} if nested else composite
    )
    before = deepcopy(tool)
    artifact, public, serialized = configuration_artifact({"functions": [tool]}, format)
    if host == "api.openweathermap.org":
        assert public["functions"] == []
        assert SECRET not in serialized
        assert artifact["security"]["omitted"] == [
            {
                "field": "function_tool_1",
                "reason": "requires_private_configuration",
                "count": 1,
            }
        ]
    else:
        assert public["functions"] == [tool]
        assert artifact["security"]["omitted"] == []
    assert tool == before


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


@pytest.mark.parametrize("kind", ["rest", "scrape", "composite"])
@pytest.mark.parametrize("location", ["schema", "params", "payload"])
@pytest.mark.parametrize("format", ["setup", "configuration"])
def test_template_provider_context_reaches_every_public_walk(
    hass, kind, location, format
):
    tool = weather_tool("default", "reference")
    node = tool["function"]
    node["resource_template"] = node.pop("resource")
    if location != "schema":
        tool["spec"]["parameters"] = {"type": "object", "properties": {}}
        node[location] = (
            {"appid": SECRET} if location == "params" else json.dumps({"appid": SECRET})
        )
    if kind == "scrape":
        node["type"] = "scrape"
        node["sensor"] = [{"name": "headline", "select": "h1"}]
    elif kind == "composite":
        tool["function"] = {"type": "composite", "sequence": [node]}
    before = deepcopy(tool)
    artifact, public, serialized = configuration_artifact({"functions": [tool]}, format)
    assert public["functions"] == []
    assert SECRET not in serialized
    assert any(v["field"] == "function_tool_1" for v in artifact["security"]["omitted"])
    assert tool == before


@pytest.mark.parametrize(
    "destination",
    ["https://public.example/api", "https://{{ host }}/api", "{{ private_endpoint }}"],
)
def test_unverifiable_or_public_templates_do_not_classify_generic_appids(
    hass, destination
):
    tool = weather_tool("default", "instance")
    tool["function"].pop("resource")
    tool["function"]["resource_template"] = destination
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        _rest_uses_credential_appid,
    )

    assert not _rest_uses_credential_appid(tool["function"])
    _, public, _ = configuration_artifact({"functions": [tool]}, "setup")
    # Dynamic destinations keep the established conservative URL-template policy.
    assert public["functions"] == (
        [tool] if destination == "https://public.example/api" else []
    )
