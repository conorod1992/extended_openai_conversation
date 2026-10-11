"""Regression coverage for saved model choices and provider capability metadata."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    merge_agent_config,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule_model_request,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_openai_sdk_catalog_matrix import _reply
from tests.test_openai_sdk_wire import (
    _chat_log,
    _client,
    _close_client,
    _entity,
    _json_body,
    _Wire,
)


@pytest.mark.parametrize("updates", [{"knowledge_enabled": True}, {"functions": []}])
def test_unrelated_save_preserves_omitted_effort(updates):
    options = {"chat_model": "gpt-5.4", "functions": []}
    rule = {"action_type": "model_routing", "action": {"model": "o3"}}
    validate_rule_model_request(rule, options, {})
    saved = merge_agent_config(options, updates)
    assert "reasoning_effort" not in saved
    validate_rule_model_request(rule, saved, {})


@pytest.mark.asyncio
async def test_catalogue_maintenance_ignores_disabled_routes(hass, monkeypatch):
    subentry = SimpleNamespace(
        subentry_type="conversation",
        subentry_id="agent",
        data={"chat_model": "gpt-5.4", "reasoning_effort": "none", "functions": []},
    )
    entry = SimpleNamespace(entry_id="entry", data={}, subentries={"agent": subentry})
    monkeypatch.setattr(hass.config_entries, "async_entries", lambda _: [entry])
    rule = {
        "enabled": False,
        "action_type": "model_routing",
        "action": {"model": "o3", "reasoning_effort": "none"},
    }

    async def rules(*_):
        return SimpleNamespace(snapshot=lambda: {"rules": [rule]})

    monkeypatch.setattr(runtime, "async_get_request_rules", rules)
    manager = runtime.ModelCatalogManager(hass)
    manager.catalog = deepcopy(BUNDLED_CATALOG)
    candidate = deepcopy(BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    assert await manager._candidate_preserves_saved_requests(candidate)
    assert not await manager._bundled_reset_would_invalidate_saved_reasoning()
    rule["enabled"] = True
    assert not await manager._candidate_preserves_saved_requests(candidate)
    assert await manager._bundled_reset_would_invalidate_saved_reasoning()


@pytest.mark.asyncio
async def test_azure_reset_uses_underlying_model(hass, monkeypatch):
    subentry = SimpleNamespace(
        subentry_type="ai_task_data",
        data={
            "chat_model": "custom-deployment",
            "azure_model": "o3",
            "reasoning_effort": "medium",
        },
    )
    entry = SimpleNamespace(
        data={"api_provider": "azure"}, subentries={"agent": subentry}
    )
    monkeypatch.setattr(hass.config_entries, "async_entries", lambda _: [entry])
    manager = runtime.ModelCatalogManager(hass)
    manager.catalog = deepcopy(BUNDLED_CATALOG)
    assert not await manager._bundled_reset_would_invalidate_saved_reasoning()


@pytest.mark.asyncio
@pytest.mark.parametrize("model,supported", [("o3-pro", True), ("gpt-4-turbo", False)])
async def test_structured_output_wire_contract(hass, model, supported):
    wire = _Wire([_reply(BUNDLED_CATALOG.resolved[model]["streaming"])])
    client = _client(wire)
    entity = _entity(hass, client, "responses" if supported else "chat_completions")
    entity.subentry.data = {
        "chat_model": model,
        "api_mode": "responses" if supported else "chat_completions",
    }

    async def run():
        await entity._async_handle_chat_log(
            _chat_log(hass),
            [],
            [],
            structure=object(),
            structure_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
            structure_name="audit",
        )

    try:
        if supported:
            await run()
            assert (
                _json_body(wire.requests[0])["text"]["format"]["type"] == "json_schema"
            )
        else:
            with pytest.raises(HomeAssistantError, match="Structured Outputs"):
                await run()
            assert not wire.requests
    finally:
        await _close_client(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,effort,supported",
    [
        ("gpt-5", None, False),
        ("gpt-5", "minimal", False),
        ("gpt-5", "low", True),
        ("gpt-5.4-pro", None, True),
        ("gpt-5.5-pro", None, True),
    ],
)
async def test_web_search_effective_wire_contract(hass, model, effort, supported):
    wire = _Wire([_reply(BUNDLED_CATALOG.resolved[model]["streaming"])])
    client = _client(wire)
    entity = _entity(hass, client, "responses")
    entity.subentry.data = {
        "chat_model": model,
        "api_mode": "responses",
        "web_search": True,
    }
    if effort is not None:
        entity.subentry.data["reasoning_effort"] = effort
    try:
        if supported:
            await entity._async_handle_chat_log(_chat_log(hass), [], [])
            body = _json_body(wire.requests[0])
            assert any(tool["type"] == "web_search" for tool in body["tools"])
            if effort:
                assert body["reasoning"]["effort"] == effort
        else:
            with pytest.raises(HomeAssistantError):
                await entity._async_handle_chat_log(_chat_log(hass), [], [])
            assert not wire.requests
    finally:
        await _close_client(client)
