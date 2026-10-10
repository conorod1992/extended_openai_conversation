"""Provider configuration boundaries from the third EOAI audit."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    backup,
    model_catalog,
    model_catalog_manager,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_strict_function_schema,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_backup import _document


@pytest.fixture(autouse=True)
def isolate_catalog():
    model_catalog.activate_catalog(None)
    yield
    model_catalog.activate_catalog(None)


def strict_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


@pytest.mark.parametrize("limit,accepted", [(10, True), (11, False)])
@pytest.mark.parametrize("references", [False, True])
def test_strict_nesting_limit_includes_referenced_definitions(limit, accepted, references):
    node = {"type": "string"}
    definitions = {}
    for index in range(limit):
        node = strict_object({"child": node})
        if references and index < limit - 1:
            definitions[f"level{index}"] = node
            node = {"$ref": f"#/$defs/level{index}"}
    if references:
        node["$defs"] = definitions
    if accepted:
        validate_strict_function_schema(node)
    else:
        with pytest.raises(HomeAssistantError, match="10 levels"):
            validate_strict_function_schema(node)


@pytest.mark.parametrize("count,accepted", [(5000, True), (5001, False)])
def test_strict_total_property_limit(count, accepted):
    node = strict_object({f"p{index}": {"type": "string"} for index in range(count)})
    if accepted:
        validate_strict_function_schema(node)
    else:
        with pytest.raises(HomeAssistantError, match="5000"):
            validate_strict_function_schema(node)


@pytest.mark.parametrize("count,accepted", [(1000, True), (1001, False)])
def test_strict_enum_limit_is_aggregate(count, accepted):
    values = [str(index) for index in range(count)]
    node = strict_object({"first": {"type": "string", "enum": values[:500]}, "second": {"type": "string", "enum": values[500:]}})
    if accepted:
        validate_strict_function_schema(node)
    else:
        with pytest.raises(HomeAssistantError, match="1000"):
            validate_strict_function_schema(node)


@pytest.mark.parametrize("size,accepted", [(120000, True), (120001, False)])
def test_strict_aggregate_string_limit(size, accepted):
    node = strict_object({"x": {"type": "string", "const": "a" * (size - 1)}})
    if accepted:
        validate_strict_function_schema(node)
    else:
        with pytest.raises(HomeAssistantError, match="120000"):
            validate_strict_function_schema(node)


def test_strict_large_single_enum_string_limit():
    node = strict_object({"value": {"type": "string", "enum": [str(index).zfill(60) for index in range(251)]}})
    with pytest.raises(HomeAssistantError, match="15000"):
        validate_strict_function_schema(node)


def test_strict_recursive_reference_is_not_expanded_forever():
    node = strict_object({"children": {"type": "array", "items": {"$ref": "#"}}})
    validate_strict_function_schema(node)


@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
async def test_azure_catalog_validation_uses_underlying_model(hass, monkeypatch, kind):
    options = {
        "chat_model": "my-deployment",
        "azure_model": "gpt-4.1-mini",
        "api_mode": "responses",
        "functions": [],
    }
    subentry = SimpleNamespace(subentry_id="agent", subentry_type=kind, data=options)
    entry = SimpleNamespace(
        entry_id="entry", data={"api_provider": "azure"}, subentries={"agent": subentry}
    )
    hass.config_entries.async_entries.return_value = [entry]
    rules = SimpleNamespace(
        snapshot=lambda: {
            "rules": [
                {
                    "action_type": "model_routing",
                    "action": {"model": "other-deployment"},
                }
            ]
        }
    )
    monkeypatch.setattr(
        model_catalog_manager, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    manager = model_catalog_manager.ModelCatalogManager(hass)
    assert await manager._candidate_preserves_saved_requests(
        model_catalog.BUNDLED_CATALOG
    )
    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False
    request = build_provider_request_snapshot(options, entry.data, tools_required=False)
    assert request.api_kwargs["model"] == "my-deployment"
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    underlying = next(
        item for item in candidate["models"] if item["id"] == "gpt-4.1-mini"
    )
    underlying["limits"]["max_output_tokens"] = 128
    assert not await manager._candidate_preserves_saved_requests(
        model_catalog.validate_catalog(candidate)
    )


def test_unknown_custom_endpoint_uses_compatible_chat_limit():
    request = build_provider_request_snapshot(
        {
            "chat_model": "mistral-small-latest",
            "api_mode": "chat_completions",
            "max_tokens": 500,
        },
        {"api_provider": "openai", "base_url": "https://api.mistral.ai/v1"},
        tools_required=False,
    )
    assert request.api_kwargs["max_tokens"] == 500
    assert "max_completion_tokens" not in request.api_kwargs


@pytest.mark.parametrize(
    "provider",
    [
        {},
        {"api_provider": "openai", "base_url": "https://api.openai.com/v1"},
        {"api_provider": "azure"},
    ],
)
def test_official_and_azure_requests_keep_modern_limit(provider):
    request = build_provider_request_snapshot(
        {
            "chat_model": "gpt-4.1-mini",
            "api_mode": "chat_completions",
            "max_tokens": 500,
        },
        provider,
        tools_required=False,
    )
    assert request.api_kwargs["max_completion_tokens"] == 500
    assert "max_tokens" not in request.api_kwargs


def test_explicit_model_mapping_accepts_chat_legacy_field_only():
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    model = next(item for item in candidate["models"] if item["id"] == "gpt-4.1-mini")
    model["output_tokens"] = {
        "responses": "max_output_tokens",
        "chat_completions": "max_tokens",
        "legacy_max_tokens": "chat_completions_only",
    }
    model_catalog.activate_catalog(model_catalog.validate_catalog(candidate))
    options = {
        "chat_model": "gpt-4.1-mini",
        "api_mode": "chat_completions",
        "max_tokens": 500,
    }
    assert (
        build_provider_request_snapshot(options, {}, tools_required=False).api_kwargs[
            "max_tokens"
        ]
        == 500
    )
    options["api_mode"] = "responses"
    assert (
        build_provider_request_snapshot(options, {}, tools_required=False).api_kwargs[
            "max_output_tokens"
        ]
        == 500
    )
    model["output_tokens"]["responses"] = "max_tokens"
    with pytest.raises(ValueError, match="output-token"):
        model_catalog.validate_catalog(candidate)


def strict_object(properties=None):
    properties = properties or {}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("violation", ["required", "additionalProperties"])
def test_saving_strict_tools_rejects_object_contract_violations(nested, violation):
    schema = strict_object({"query": {"type": "string"}})
    if violation == "required":
        schema[violation] = []
    else:
        schema.pop(violation)
    if nested:
        schema = strict_object({"filters": {"type": "array", "items": schema}})
    tool = {
        "spec": {"name": "demo", "strict": True, "parameters": schema},
        "function": {"type": "template", "value_template": "{{ query }}"},
    }
    with pytest.raises(agent_config.AgentConfigError, match=violation) as raised:
        agent_config.normalize_agent_config({"functions": [tool]})
    assert "parameters" in str(raised.value)
    if nested:
        assert "items" in str(raised.value)


def test_valid_nullable_nested_strict_tool_is_preserved():
    inner = strict_object({"query": {"type": ["string", "null"]}})
    schema = strict_object({"filters": {"type": "array", "items": inner}})
    tool = {
        "spec": {"name": "demo", "strict": True, "parameters": schema},
        "function": {"type": "template", "value_template": "{{ filters }}"},
    }
    result = agent_config.normalize_agent_config({"functions": [tool]})
    assert yaml.safe_load(result["functions"])[0]["spec"]["parameters"] == schema


def test_strict_validation_reaches_union_branches_and_definitions():
    for keyword, child in [
        ("anyOf", [{"type": "object"}]),
        ("$defs", {"filter": {"type": "object"}}),
    ]:
        schema = strict_object()
        if keyword == "anyOf":
            schema = strict_object({"nested": {"anyOf": child}})
        else:
            schema[keyword] = child
        with pytest.raises(HomeAssistantError, match="additionalProperties"):
            validate_strict_function_schema(schema)


@pytest.mark.parametrize("explicit", [None, "minimal"])
@pytest.mark.parametrize("quarantined", [False, True])
def test_backup_roundtrip_preserves_dynamic_reasoning_default(explicit, quarantined):
    config = {"chat_model": "gpt-5-mini", "functions": []}
    if explicit:
        config["reasoning_effort"] = explicit
    if quarantined:
        config["functions"] = [
            {"spec": {"name": "broken"}, "function": {"type": "unknown"}}
        ]
    original = deepcopy(config)
    exported = backup.export_configuration_snapshot(config)
    document = _document()
    document["agent"]["config"] = exported
    restored = backup.inspect_backup(document, "new-agent").config
    assert config == original
    if explicit:
        assert restored["reasoning_effort"] == explicit
    else:
        assert "reasoning_effort" not in exported
        assert "reasoning_effort" not in restored
        routed = {**restored, "chat_model": "gpt-5-pro", "functions": []}
        request = build_provider_request_snapshot(routed, {}, tools_required=False)
        assert request.api_kwargs["reasoning"]["effort"] == "high"


@pytest.mark.parametrize("keyword,value", [
    ("not", {"const": "blocked"}), ("allOf", [{"type": "string"}]),
    ("oneOf", [{"type": "string"}]), ("if", {"const": "x"}),
    ("then", {"const": "y"}), ("else", {"const": "z"}),
    ("dependentRequired", {"a": ["b"]}), ("dependentSchemas", {"a": {}}),
])
def test_strict_rejects_unsupported_composition_at_save(keyword, value):
    schema = {"type": "object", "properties": {"query": {"type": "string", keyword: value}},
              "required": ["query"], "additionalProperties": False}
    tool = {"spec": {"name": "demo", "strict": True, "parameters": schema},
            "function": {"type": "native", "name": "execute_service"}}
    with pytest.raises(HomeAssistantError, match=keyword):
        agent_config.normalize_agent_config({"functions": [tool]})
    tool["spec"]["strict"] = False
    assert agent_config.normalize_agent_config({"functions": [tool]})["functions"]


def test_strict_root_anyof_rejected_but_nested_unions_and_references_supported():
    branch = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    with pytest.raises(HomeAssistantError, match="root"):
        validate_strict_function_schema({**branch, "anyOf": [branch]})
    validate_strict_function_schema({
        "type": "object", "properties": {"node": {"anyOf": [{"$ref": "#/$defs/node"}, {"type": "null"}]}},
        "required": ["node"], "additionalProperties": False,
        "$defs": {"node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/node"}},
                           "required": ["next"], "additionalProperties": False}},
    })
