"""Credential repair followed by an actual conversation provider-wire request."""
from unittest.mock import AsyncMock, patch

from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION, API_MODE_CHAT_COMPLETIONS,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text, _install_wire, _say, _speech,
)


async def test_reauthentication_keeps_agent_usable_and_refreshes_wire_auth(hass, monkeypatch):
    """Repair credential via genuine HA flow, then call the repaired agent."""
    entry = _make_entry(
        "Reauth end-to-end",
        include_ai_task=False,
        data={CONF_API_KEY: "sk-expired-journey", CONF_SKIP_AUTHENTICATION: True},
        conversation_options={"api_mode": API_MODE_CHAT_COMPLETIONS, "chat_model": "gpt-5.6"},
    )
    await _setup_entry(hass, entry)
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
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Credential repaired.")])
    assert _speech(await _say(hass, agent)) == "Credential repaired."
    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    # Assert the actual SDK request uses the newly persisted credential,
    # not merely that the configuration entry displays it.
    request_headers = wire.requests[0].get("headers", {})
    assert "sk-replacement-journey" in str(request_headers)
    assert "sk-expired-journey" not in str(request_headers)
