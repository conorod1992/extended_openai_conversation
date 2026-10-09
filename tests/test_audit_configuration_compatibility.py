"""Configuration audit regressions using production request/schema builders."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from custom_components.extended_openai_conversation_responses.ai_task import _omit_optional_nulls
from custom_components.extended_openai_conversation_responses.entity import _adjust_schema
from custom_components.extended_openai_conversation_responses.ha_llm_tools import caller_api_tools
from custom_components.extended_openai_conversation_responses.model_capabilities import normalize_output_token_limit, ModelCapabilityError
from custom_components.extended_openai_conversation_responses.request import build_provider_request_snapshot


def test_reference_cleanup_retains_root_context_and_nullable_fields():
    schema = {"type": "object", "properties": {"person": {"$ref": "#/$defs/person"}}, "$defs": {"person": {"type": "object", "properties": {"nickname": {"type": "string"}, "address": {"$ref": "#/$defs/address"}}, "additionalProperties": False}, "address": {"type": ["string", "null"]}}}
    data = {"person": {"nickname": None, "address": None}}
    cleaned = _omit_optional_nulls(data, schema)
    assert cleaned == {"person": {"address": None}}
    Draft202012Validator(schema).validate(cleaned)
    assert data["person"]["nickname"] is None


def test_prompt_only_caller_api_retains_its_instructions():
    api = SimpleNamespace(api=SimpleNamespace(name="Caller"), api_prompt="The alarm is called Aster", tools=[])
    snapshot, tools = caller_api_tools(api)
    assert tools == []
    assert snapshot.prompt_for(tools) == api.api_prompt


def test_ha_selector_annotations_are_removed_from_provider_schema_only():
    schema = {"type": "object", "properties": {"entity": {"type": "string", "format": "entity_id"}, "entities": {"type": "array", "items": {"type": "string", "format": "entity_id"}, "uniqueItems": True}}}
    original = deepcopy(schema)
    _adjust_schema(schema, root=True)
    assert "format" not in schema["properties"]["entity"]
    assert "uniqueItems" not in schema["properties"]["entities"]
    assert original["properties"]["entity"]["format"] == "entity_id"


def test_explicit_legacy_default_temperature_is_sent():
    options = {"chat_model": "gpt-4.1", "api_mode": "responses", "temperature": 0.5}
    assert build_provider_request_snapshot(options, {}, tools_required=False).api_kwargs["temperature"] == 0.5
    options.pop("temperature")
    assert "temperature" not in build_provider_request_snapshot(options, {}, tools_required=False).api_kwargs


@pytest.mark.parametrize("limit", [1, 15])
def test_responses_minimum_is_api_specific(limit):
    with pytest.raises(ModelCapabilityError, match="at least 16"):
        normalize_output_token_limit("gpt-4.1", "responses", limit)
    assert normalize_output_token_limit("gpt-4.1", "chat_completions", limit)[1] == limit


async def test_configuration_replacement_rejects_retained_rule_dependency(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import management_ui
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from tests.test_request_rules import local_rule, manager
    rule = local_rule()
    rule["action"]["actions"] = [{"action": f"{DOMAIN}.call_function", "data": {"function": "retained_function", "arguments": {}}}]
    rules = await manager(rule)
    async def load(*args):
        return rules
    monkeypatch.setattr(management_ui, "async_get_request_rules", load)
    with pytest.raises(Exception, match="retained_function"):
        await management_ui._async_validate_configuration_dependencies(hass, SimpleNamespace(entry_id="entry"), SimpleNamespace(subentry_id="agent"), {"functions": []})


async def test_fast_save_uses_canonical_title_limit():
    from custom_components.extended_openai_conversation_responses.management_ui import _async_save_configuration
    request = SimpleNamespace(hass=None, is_admin=True, message={"title": "x" * 256}, entry=None, subentry=None)
    assert (await _async_save_configuration(request))["valid"] is False


async def test_repair_rejects_incompatible_api_before_writing():
    from custom_components.extended_openai_conversation_responses import management_function_repair as repair
    from tests.test_management_function_repair import _mixed_legacy_tool_data, _entry_and_subentry, _FakeConfigEntries
    data, tools, _ = _mixed_legacy_tool_data()
    data.update(chat_model="gpt-6-astra", api_mode="chat_completions")
    entry, subentry = _entry_and_subentry(data)
    entries = _FakeConfigEntries()
    with pytest.raises(Exception, match="calling|API|support"):
        repair._persist_raw_tools(SimpleNamespace(config_entries=entries), entry, subentry, tools, [])
    assert entries.updates == 0
