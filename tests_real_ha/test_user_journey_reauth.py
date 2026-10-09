"""Credential repair reaches conversation, AI Task and embedding SDK requests."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_SKIP_AUTHENTICATION,
    DOMAIN,
)
from homeassistant.components import ai_task, conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _responses_sse_text,
    _say,
    _speech,
)


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_reauthentication_keeps_agent_usable_and_refreshes_wire_auth(
    hass, monkeypatch, api_mode
):
    """Repair through HA, then inspect credentials on three real SDK paths."""
    entry = _make_entry(
        "Reauth end-to-end",
        data={CONF_API_KEY: "sk-expired-journey", CONF_SKIP_AUTHENTICATION: True},
        conversation_options={"api_mode": api_mode, "chat_model": "gpt-5.6"},
    )
    await _setup_entry(hass, entry)
    task_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "ai_task_data"
    )
    hass.config_entries.async_update_subentry(
        entry,
        task_sub,
        data={
            **task_sub.data,
            "api_mode": api_mode,
            "chat_model": "gpt-5.6",
            "reasoning_effort": "none",
        },
    )
    await hass.async_block_till_done()
    before = set(entry.subentries)
    with patch(
        "custom_components.extended_openai_conversation_responses.config_flow.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        flow = await entry.start_reauth_flow(hass)
        assert flow["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_API_KEY: "sk-replacement-journey"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert set(entry.subentries) == before
    assert entry.data[CONF_API_KEY] == "sk-replacement-journey"
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    text_reply = (
        _chat_sse_text if api_mode == API_MODE_CHAT_COMPLETIONS else _responses_sse_text
    )
    replies = [text_reply("Credential repaired."), text_reply("Task repaired.")]
    endpoint = (
        "/v1/chat/completions"
        if api_mode == API_MODE_CHAT_COMPLETIONS
        else "/v1/responses"
    )
    replies.append(
        (
            200,
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )
    )
    wire = _install_wire(monkeypatch, agent, replies)
    observed_auth = []

    async def capture_auth(request, *args, **kwargs):
        observed_auth.append(request.headers.get("authorization", ""))
        return await wire.send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", capture_auth)
    assert _speech(await _say(hass, agent)) == "Credential repaired."
    task_id = er.async_get(hass).async_get_entity_id(
        ai_task.DOMAIN, DOMAIN, task_sub.subentry_id
    )
    assert task_id is not None
    task_result = await ai_task.async_generate_data(
        hass,
        task_name="Repaired credential task",
        entity_id=task_id,
        instructions="Return a short result",
    )
    assert task_result.data == "Task repaired."
    assert await agent._async_create_embeddings(["Credential embedding probe"]) == [
        [1.0, 0.0]
    ]
    assert [request["path"] for request in wire.requests] == [
        endpoint,
        endpoint,
        "/v1/embeddings",
    ]
    # Inspect real SDK headers, not just entry configuration or mocked create().
    assert observed_auth == ["Bearer sk-replacement-journey"] * 3
