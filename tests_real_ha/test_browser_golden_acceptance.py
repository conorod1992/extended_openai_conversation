"""Native UI authoring, durable reload and provider-wire semantic golden journeys."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    real_ha_shell as real_ha_shell,
)
from tests_real_ha.test_knowledge_provider_wire_e2e import _chat_sse_tool_call
from tests_real_ha.test_management_backend_acceptance import _entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="native shell golden journeys belong in browser acceptance/nightly",
)


@pytest.mark.parametrize("feature", ["tool", "context"])
async def test_native_shell_golden_runtime_journey(hass, real_ha_shell, monkeypatch, feature):
    shell = real_ha_shell
    entry = shell["entry"]
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, subentry, data={
        **subentry.data, CONF_API_MODE: "chat_completions", CONF_FUNCTION_TOOLS: [],
        CONF_FUNCTION_GROUPS: [], CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
    })
    await hass.async_block_till_done()
    secondary = _entry("Golden secondary assistant")
    await _setup_entry(hass, secondary)
    phases = ["create", "edit", "disable"] if feature == "tool" else ["create", "disable"]
    for phase in phases:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parents[1],
            spec="tests_browser/real-ha-golden.spec.mjs",
            config="playwright.real-ha-shell.config.mjs",
            env={**shell["env"], "REAL_HA_GOLDEN_FEATURE": feature, "REAL_HA_GOLDEN_PHASE": phase, "PLAYWRIGHT_ARTIFACT_SUFFIX": f"golden-{feature}-{phase}"},
            failure_label=f"Native shell golden {feature}/{phase} failed",
        )
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        enabled = phase != "disable"
        name = "golden_shell_tool" if feature == "tool" else "knowledge_search"
        replies = []
        if enabled:
            arguments = {} if feature == "tool" else {"query": "golden observatory", "limit": 3}
            replies.append(_chat_sse_tool_call("call-golden", name, arguments))
        expected = f"Golden {feature} {phase} complete"
        replies.append(_chat_sse_text(expected))
        wire = _install_wire(monkeypatch, agent, replies)
        result = await conversation.async_converse(
            hass=hass, text="golden observatory token and calibration", conversation_id=None,
            context=Context(user_id=shell["admin"].id), language="en", agent_id=entry.entry_id,
        )
        assert _speech(result) == expected
        assert len(wire.requests) == (2 if enabled else 1)
        first = wire.requests[0]["body"]
        advertised = {tool["function"]["name"] for tool in first.get("tools", [])}
        assert (name in advertised) is enabled
        if feature == "context":
            assert ("cobalt-golden-memory" in json.dumps(first["messages"])) is enabled
        if enabled:
            tool_messages = [item for item in wire.requests[1]["body"]["messages"] if item.get("role") == "tool"]
            assert len(tool_messages) == 1
            marker = f"golden-tool-{'v1' if phase == 'create' else 'v2'}" if feature == "tool" else "cobalt-golden-knowledge"
            assert marker in tool_messages[0]["content"]
        # A save through the first assistant must not affect its sibling.
        sibling = conversation.async_get_agent(hass, secondary.entry_id)
        assert sibling is not None
        assert all(tool.get("spec", {}).get("name") != "golden_shell_tool" for tool in sibling.subentry.data.get(CONF_FUNCTION_TOOLS, []))

async def test_native_api_key_replacement_preserves_or_reloads_provider_family(hass, real_ha_shell, monkeypatch):
    """The browser's native WS command validates and reloads the actual parent."""
    from types import MappingProxyType

    import httpx

    from custom_components.extended_openai_conversation_responses.const import (
        CONF_CHAT_MODEL,
        CONF_SKIP_AUTHENTICATION,
        DEFAULT_AI_TASK_OPTIONS,
        DOMAIN,
    )
    from homeassistant.components import ai_task
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.const import CONF_API_KEY
    from homeassistant.helpers import entity_registry as er
    from tests_real_ha.test_provider_wire_e2e import _raw_client

    shell = real_ha_shell
    entry = shell["entry"]
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, subentry, data={
        **subentry.data, CONF_API_MODE: "chat_completions", CONF_CHAT_MODEL: "gpt-5.6",
        CONF_FUNCTION_TOOLS: [], CONF_FUNCTION_GROUPS: [],
    })
    task = ConfigSubentry(
        data=MappingProxyType({**DEFAULT_AI_TASK_OPTIONS, CONF_API_MODE: "chat_completions", CONF_CHAT_MODEL: "gpt-5.6"}),
        subentry_type="ai_task_data", title="Credential sibling task", unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(entry, task)
    await hass.async_block_till_done()
    secondary = _entry("Unrelated provider connection")
    secondary.add_to_hass(hass)
    secondary_subentry = next(iter(secondary.subentries.values()))
    hass.config_entries.async_update_subentry(secondary, secondary_subentry, data={**secondary_subentry.data, CONF_API_MODE: "chat_completions", CONF_CHAT_MODEL: "gpt-5.6", CONF_FUNCTION_TOOLS: [], CONF_FUNCTION_GROUPS: []})
    await _setup_entry(hass, secondary)
    unrelated = conversation.async_get_agent(hass, secondary.entry_id)
    unrelated_client = secondary.runtime_data
    original_key = entry.data[CONF_API_KEY]
    calls = []

    async def send(request, *args, **kwargs):
        del args, kwargs
        auth = request.headers.get("authorization")
        calls.append((request.method, request.url.path, auth))
        if auth == "Bearer sk-browser-invalid":
            return httpx.Response(401, json={"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}}, request=request)
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-5.6", "object": "model", "created": 0, "owned_by": "openai"}]}, request=request)
        if not json.loads(request.content).get("stream", False):
            return httpx.Response(200, json={"id": "chatcmpl-credential-diagnostics", "object": "chat.completion", "created": 0, "model": "gpt-5.6", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}]}, request=request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_chat_sse_text("Credential runtime accepted"), request=request)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    # Enabling validation is itself a real entry update; allow its reload to settle.
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SKIP_AUTHENTICATION: False})
    await hass.async_block_till_done()
    original = conversation.async_get_agent(hass, entry.entry_id)
    original_client = entry.runtime_data
    task_id = er.async_get(hass).async_get_entity_id(ai_task.DOMAIN, DOMAIN, task.subentry_id)
    assert task_id is not None
    for phase in ("invalid", "valid"):
        await _run_playwright(
            repo_root=Path(__file__).resolve().parents[1],
            spec="tests_browser/real-ha-credential-replacement.spec.mjs",
            config="playwright.real-ha-shell.config.mjs",
            env={**shell["env"], "REAL_HA_CREDENTIAL_PHASE": phase, "REAL_HA_CREDENTIAL_AGENT": subentry.subentry_id},
            failure_label=f"Native API key replacement {phase} failed",
        )
        await hass.async_block_till_done()
        current = conversation.async_get_agent(hass, entry.entry_id)
        expected_key = original_key if phase == "invalid" else "sk-browser-replacement"
        assert entry.data[CONF_API_KEY] == expected_key
        if phase == "invalid":
            assert current is original
            assert entry.runtime_data is original_client
        else:
            assert current is not original
            assert entry.runtime_data is not original_client
        start = len(calls)
        result = await conversation.async_converse(
            hass=hass, text="credential sibling probe", conversation_id=None,
            context=Context(user_id=shell["admin"].id), language="en", agent_id=entry.entry_id,
        )
        assert _speech(result) == "Credential runtime accepted"
        generated = await ai_task.async_generate_data(hass, task_name="Credential sibling", entity_id=task_id, instructions="credential sibling probe")
        assert generated.data == "Credential runtime accepted"
        posts = [call for call in calls[start:] if call[0] == "POST"]
        assert posts == [("POST", "/v1/chat/completions", f"Bearer {expected_key}")] * 2
        assert conversation.async_get_agent(hass, secondary.entry_id) is unrelated
        assert secondary.runtime_data is unrelated_client
        assert secondary.data[CONF_API_KEY] == "sk-management-backend-acceptance"
        unrelated_result = await conversation.async_converse(hass=hass, text="unrelated parent probe", conversation_id=None, context=Context(), language="en", agent_id=secondary.entry_id)
        assert _speech(unrelated_result) == "Credential runtime accepted"
        assert calls[-1] == ("POST", "/v1/chat/completions", "Bearer sk-management-backend-acceptance")
    assert ("GET", "/v1/models", "Bearer sk-browser-invalid") in calls
    assert ("GET", "/v1/models", "Bearer sk-browser-replacement") in calls
