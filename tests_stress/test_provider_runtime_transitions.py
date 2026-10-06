"""Provider/runtime configuration transition acceptance."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    CONF_SKIP_AUTHENTICATION,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    async_replace_api_key,
)
from homeassistant.components import conversation
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_config_flow import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _responses_sse_text,
    _speech,
)
from tests_stress.conftest import record


async def _converse(hass, agent, text, conversation_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.asyncio
async def test_established_conversation_survives_api_and_model_capability_transitions(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    agent = await _agent(
        hass,
        **{
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "none",
        },
    )
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("first reply")])
    first = await _converse(hass, agent, "first transition turn")
    assert _speech(first) == "first reply"
    assert first.conversation_id
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    assert wire.requests[0]["body"]["model"] == "gpt-5.6"

    subentry = agent.subentry
    hass.config_entries.async_update_subentry(
        agent.entry,
        subentry,
        data={
            **subentry.data,
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5-mini",
            CONF_REASONING_EFFORT: "low",
        },
    )
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent is not None

    second_wire = _install_wire(monkeypatch, agent, [_responses_sse_text("second reply")])
    second = await _converse(
        hass,
        agent,
        "second transition turn",
        conversation_id=first.conversation_id,
    )
    assert _speech(second) == "second reply"
    assert second.conversation_id == first.conversation_id
    assert second_wire.requests[0]["path"] == "/v1/responses"
    assert second_wire.requests[0]["body"]["model"] == "gpt-5-mini"
    payload = json.dumps(second_wire.requests[0]["body"], ensure_ascii=False)
    assert "first transition turn" in payload
    assert "first reply" in payload
    assert "second transition turn" in payload
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist + SDK wire",
        established_api_mode_transitions=1,
        established_model_transitions=1,
        preserved_history_probes=1,
    )


@pytest.mark.asyncio
async def test_parent_provider_family_base_url_and_credentials_change_next_wire(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    entry = _make_entry(
        "Runtime provider transition",
        data={
            CONF_API_KEY: "old-key",
            CONF_API_PROVIDER: "openai",
            CONF_BASE_URL: "https://old-compatible.example/v1",
            CONF_SKIP_AUTHENTICATION: True,
        },
        conversation_data={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "none",
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    old_requests = []
    async def old_send(request, *args, **kwargs):
        old_requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_text("old endpoint"),
            request=request,
        )
    monkeypatch.setattr(_raw_client(agent)._client, "send", old_send)
    assert _speech(await _converse(hass, agent, "old endpoint turn")) == "old endpoint"
    assert old_requests[0].url.host == "old-compatible.example"
    assert old_requests[0].headers["authorization"] == "Bearer old-key"

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        rotated = await async_replace_api_key(hass, entry, "rotated-key")
    assert rotated["updated"] is True
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_API_PROVIDER: "azure",
            CONF_BASE_URL: "https://new-family.example",
            CONF_API_VERSION: "2025-01-01-preview",
        },
    )
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    new_requests = []
    async def new_send(request, *args, **kwargs):
        new_requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_text("new endpoint"),
            request=request,
        )
    monkeypatch.setattr(_raw_client(agent)._client, "send", new_send)
    assert _speech(await _converse(hass, agent, "new endpoint turn")) == "new endpoint"
    request = new_requests[0]
    assert request.url.host == "new-family.example"
    assert "/openai/deployments/gpt-5.6/chat/completions" in request.url.path
    assert request.headers["api-key"] == "rotated-key"
    assert "authorization" not in request.headers
    assert request.url.params["api-version"] == "2025-01-01-preview"
    record(
        stress_trace,
        "summary",
        layer="parent config reload + installed SDK wire",
        credential_rotations=1,
        provider_family_transitions=1,
        base_url_transitions=1,
    )


@pytest.mark.asyncio
async def test_private_storage_unreadable_at_boot_recovers_without_overwriting_state(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    entry = _make_entry(
        "Private storage recovery",
        conversation_data={CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None and agent._memory is not None
    await agent._memory.async_add(
        "storage-owner",
        "PERSISTED_BEFORE_PERMISSION_FAILURE",
        "recovery",
        "explicit",
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    real_load = HomeAssistantMemoryStorage.async_load
    real_save = HomeAssistantMemoryStorage.async_save
    saves = 0

    async def denied_load(self):
        raise PermissionError("controlled private storage permission failure")

    async def observed_save(self, data):
        nonlocal saves
        saves += 1
        return await real_save(self, data)

    monkeypatch.setattr(HomeAssistantMemoryStorage, "async_load", denied_load)
    monkeypatch.setattr(HomeAssistantMemoryStorage, "async_save", observed_save)
    loaded = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert loaded is False or entry.state is not ConfigEntryState.LOADED
    assert saves == 0

    monkeypatch.setattr(HomeAssistantMemoryStorage, "async_load", real_load)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None and agent._memory is not None
    memories = await agent._memory.async_list("storage-owner")
    assert [item.content for item in memories] == [
        "PERSISTED_BEFORE_PERMISSION_FAILURE"
    ]
    record(
        stress_trace,
        "summary",
        layer="Real HA startup + private Store",
        unreadable_boots=1,
        writes_during_inaccessible_boot=saves,
        repaired_boots=1,
    )


@pytest.mark.asyncio
async def test_first_time_config_flow_reaches_loaded_agent_and_first_assist_request(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A genuinely created first installation must load and answer its first turn."""
    from custom_components.extended_openai_conversation_responses.const import (
        DEFAULT_CONF_BASE_URL,
        DOMAIN,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "First Setup Journey",
            CONF_API_KEY: "first-setup-key",
            CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
            CONF_SKIP_AUTHENTICATION: True,
            CONF_API_PROVIDER: "openai",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.title == "First Setup Journey"
    ]
    assert len(entries) == 1
    entry = entries[0]
    if entry.state is not ConfigEntryState.LOADED:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert {sub.subentry_type for sub in entry.subentries.values()} == {
        "conversation",
        "ai_task_data",
    }
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("first setup answered")])
    result = await _converse(hass, agent, "hello from a new installation")
    assert _speech(result) == "first setup answered"
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="HA config flow + loaded integration + SDK wire",
        first_install_journeys=1,
        default_subentries=2,
        first_assist_requests=1,
    )
