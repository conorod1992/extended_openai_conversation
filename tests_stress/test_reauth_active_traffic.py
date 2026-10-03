"""Nightly reauthentication while provider traffic is active."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.extended_openai_conversation_responses import helpers
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_API_MODE,
    CONF_SKIP_AUTHENTICATION,
    DOMAIN,
    SERVICE_QUERY_IMAGE,
)
from homeassistant.components import conversation
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
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
@pytest.mark.parametrize("entrypoint,outcome", [
    ("assist", "success"), ("assist", "stale-401"), ("query_image", "stale-401")
])
async def test_reauth_replaces_provider_client_without_crossing_active_traffic(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    entrypoint: str,
    outcome: str,
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
        if outcome == "stale-401":
            return httpx.Response(401, json={"error": {
                "message": "Old credentials are invalid", "type": "invalid_request_error",
                "code": "invalid_api_key"}}, request=response.request)
        return response

    monkeypatch.setattr(old_raw._client, "send", blocked_old_send)
    if entrypoint == "query_image":
        active = asyncio.create_task(hass.services.async_call(
            DOMAIN, SERVICE_QUERY_IMAGE, {"config_entry": entry.entry_id,
                "model": "gpt-4.1-mini", CONF_API_MODE: "chat_completions",
                "prompt": "What is shown?", "images": [{"url": "https://example.com/dog.png"}],
                "max_tokens": 123}, blocking=True, return_response=True))
    else:
        active = asyncio.create_task(conversation.async_converse(
            hass=hass,
            text="Keep the old provider request in flight",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        ))
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
        monkeypatch, new_agent, [_chat_sse_text("New credential client is healthy."),
                                 _chat_sse_text("Replacement remains healthy.")]
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
    if entrypoint == "query_image":
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(active, timeout=15)
    else:
        old_result = await asyncio.wait_for(active, timeout=15)
        if outcome == "success":
            assert _speech(old_result) == "Old credential turn completed."
        else:
            assert old_result.response.error_code is not None
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id})
    assert entry.data[CONF_API_KEY] == "sk-nightly-replacement"
    assert conversation.async_get_agent(hass, entry.entry_id) is new_agent
    assert len(new_wire.requests) == 1
    assert len(old_wire.requests) == 1
    subsequent = await conversation.async_converse(hass=hass, text="Confirm repaired connection",
        conversation_id=None, context=Context(), language="en", agent_id=entry.entry_id)
    assert _speech(subsequent) == "Replacement remains healthy."
    assert len(new_wire.requests) == 2

    # A failure from the current unchanged credential must still request reauth.
    current_wire = _install_wire(monkeypatch, new_agent, [(401, {"error": {
        "message": "Current credentials are invalid", "type": "invalid_request_error",
        "code": "invalid_api_key"}})])
    current_failure = await conversation.async_converse(hass=hass, text="Check current credentials",
        conversation_id=None, context=Context(), language="en", agent_id=entry.entry_id)
    assert current_failure.response.error_code is not None
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id})
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"
    assert len(current_wire.requests) == 1

    record(
        stress_trace,
        "summary",
        layer="Real HA config flow + provider wire",
        active_reauth_replacements=1,
        provider_requests=4,
        stale_authentication_failures=int(outcome == "stale-401"),
        current_authentication_failures=1,
        entrypoint=entrypoint,
    )
