"""Ordinary settings save keeps the real HA agent and provider client live."""

from unittest.mock import AsyncMock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    get_request_rule_runtime,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech


async def _live_agent(hass):
    options = normalize_agent_config(
        {"chat_model": "gpt-4o", "api_mode": "chat_completions", "functions": []}
    )
    entry = _make_entry(
        "Live save", include_ai_task=False, conversation_options=options
    )
    await _setup_entry(hass, entry)
    return conversation.async_get_agent(hass, entry.entry_id)


@pytest.mark.parametrize(
    "settings", [{"max_tokens": 777}, {"temperature": 0.3, "top_p": 0.8}]
)
async def test_live_save_preserves_real_runtime_and_next_provider_request(
    hass, monkeypatch, settings
):
    agent = await _live_agent(hass)
    entry, subentry = agent.entry, agent.subentry
    client = entry.runtime_data
    rules = get_request_rule_runtime(hass, entry.entry_id, subentry.subentry_id)
    owner = await hass.auth.async_create_user(
        "Live configuration owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
    }
    before = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    authenticate = AsyncMock(
        side_effect=AssertionError("live Save must not authenticate again")
    )
    monkeypatch.setattr(integration, "get_authenticated_client", authenticate)
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Saved settings applied")])
    saved = await async_management_command(
        hass,
        owner.id,
        True,
        {**base, "action": "save", "revision": before["revision"], "config": settings},
    )
    assert saved["valid"]
    assert saved["_performance"]["live_runtime_update"]
    # Let HA run the genuine registered update listener before inspecting identity.
    await hass.async_block_till_done()
    assert conversation.async_get_agent(hass, entry.entry_id) is agent
    assert entry.runtime_data is client
    assert get_request_rule_runtime(hass, entry.entry_id, subentry.subentry_id) is rules
    readback = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    assert readback["revision"] == saved["revision"]
    result = await conversation.async_converse(
        hass=hass,
        text="Use the saved settings",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(result) == "Saved settings applied"
    for key, value in settings.items():
        wire_key = "max_completion_tokens" if key == "max_tokens" else key
        assert wire.requests[0]["body"][wire_key] == value
        assert readback["config"][key] == value
    authenticate.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [{"title": "New device name"}, {"config": {"chat_model": "gpt-4.1-mini"}}],
)
async def test_setup_owned_changes_still_reload_the_real_agent(hass, changes):
    agent = await _live_agent(hass)
    entry = agent.entry
    owner = await hass.auth.async_create_user(
        "Reload configuration owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
    }
    before = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    saved = await async_management_command(
        hass,
        owner.id,
        True,
        {**base, "action": "save", "revision": before["revision"], **changes},
    )
    assert saved["valid"]
    assert not saved["_performance"]["live_runtime_update"]
    await hass.async_block_till_done()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement is not None and replacement is not agent
    assert replacement.entry.runtime_data is not agent._client
    if "title" in changes:
        assert replacement.device_info["name"] == changes["title"]
    else:
        assert (
            replacement.subentry.data["chat_model"] == changes["config"]["chat_model"]
        )
        assert replacement.device_info["model"] == changes["config"]["chat_model"]


async def test_entry_credential_update_after_live_save_still_reauthenticates(
    hass, monkeypatch
):
    agent = await _live_agent(hass)
    entry = agent.entry
    owner = await hass.auth.async_create_user(
        "Credential owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
    }
    before = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    authenticate = AsyncMock(wraps=integration.get_authenticated_client)
    monkeypatch.setattr(integration, "get_authenticated_client", authenticate)
    saved = await async_management_command(
        hass,
        owner.id,
        True,
        {
            **base,
            "action": "save",
            "revision": before["revision"],
            "config": {"max_tokens": 777},
        },
    )
    assert saved["valid"] and saved["_performance"]["live_runtime_update"]
    await hass.async_block_till_done()
    authenticate.assert_not_awaited()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "api_key": "sk-changed-acceptance-only"}
    )
    await hass.async_block_till_done()
    authenticate.assert_awaited_once()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement is not None and replacement is not agent
