"""Nightly reauthentication while provider traffic is active."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.extended_openai_conversation_responses import helpers
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_SKIP_AUTHENTICATION,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _speech,
)
from tests_stress.conftest import record

CONFIG_FLOW = "custom_components.extended_openai_conversation_responses.config_flow"
INTEGRATION = "custom_components.extended_openai_conversation_responses"


@pytest.mark.asyncio
async def test_reauth_replaces_provider_client_without_crossing_active_traffic(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """An old in-flight client cannot become the client of the reloaded agent."""
    entry = _make_entry("Active reauth", include_ai_task=False)
    await _setup_entry(hass, entry)
    old_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert old_agent is not None
    old_raw = _raw_client(old_agent)

    old_wire = _install_wire(
        monkeypatch, old_agent, [_chat_sse_text("Old credential turn completed.")]
    )
    old_send = old_wire.send
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_old_send(*args, **kwargs):
        response = await old_send(*args, **kwargs)
        entered.set()
        await release.wait()
        return response

    monkeypatch.setattr(old_raw._client, "send", blocked_old_send)
    active = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Keep the old provider request in flight",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=10)

    replacement_client = await helpers.get_authenticated_client(
        hass=hass,
        api_key="sk-nightly-replacement",
        base_url=None,
        api_version=None,
        organization=None,
        api_provider="openai",
        skip_authentication=True,
    )
    with (
        patch(f"{CONFIG_FLOW}.validate_input", AsyncMock(return_value=None)),
        patch(
            f"{INTEGRATION}.get_authenticated_client",
            AsyncMock(return_value=replacement_client),
        ),
    ):
        flow = await entry.start_reauth_flow(hass)
        assert flow["type"] is FlowResultType.FORM
        finished = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_API_KEY: "sk-nightly-replacement"}
        )
        assert finished["type"] is FlowResultType.ABORT
        assert finished["reason"] == "reauth_successful"
        await hass.async_block_till_done()

    assert entry.data[CONF_API_KEY] == "sk-nightly-replacement"
    assert entry.data.get(CONF_API_PROVIDER, "openai") == "openai"
    assert entry.data.get(CONF_SKIP_AUTHENTICATION, False) is True
    new_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert new_agent is not None and new_agent is not old_agent
    assert _raw_client(new_agent) is replacement_client

    new_wire = _install_wire(
        monkeypatch, new_agent, [_chat_sse_text("New credential client is healthy.")]
    )
    fresh = await conversation.async_converse(
        hass=hass,
        text="Use the replacement provider client",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(fresh) == "New credential client is healthy."
    assert len(new_wire.requests) == 1
    assert len(old_wire.requests) == 1

    release.set()
    old_result = await asyncio.wait_for(active, timeout=15)
    assert _speech(old_result) == "Old credential turn completed."
    assert conversation.async_get_agent(hass, entry.entry_id) is new_agent
    assert len(new_wire.requests) == 1
    assert len(old_wire.requests) == 1

    record(
        stress_trace,
        "summary",
        layer="Real HA config flow + provider wire",
        active_reauth_replacements=1,
        provider_requests=2,
    )
