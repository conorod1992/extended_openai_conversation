"""Configuration projection contracts through real HA and serialized SDK requests."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech


@pytest.mark.parametrize("explicit_sampling", [False, True])
async def test_section_save_preserves_sampling_presence_but_transfer_materializes_defaults(
    hass, monkeypatch, explicit_sampling
):
    options = {"chat_model": "gpt-4.1", "api_mode": "chat_completions", "functions": []}
    defaults = agent_config_defaults()
    if explicit_sampling:
        options.update(temperature=defaults["temperature"], top_p=defaults["top_p"])
    entry = _make_entry(
        "Projection contracts", include_ai_task=False, conversation_options=options
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    subentry = agent.subentry
    original = deepcopy(dict(subentry.data))
    owner = await hass.auth.async_create_user(
        "Projection owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
    }
    before = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    for key in ("temperature", "top_p"):
        assert before["config"][key] == defaults[key]
        assert (key in subentry.data) is explicit_sampling
    saved = await async_management_command(
        hass,
        owner.id,
        True,
        {
            **base,
            "action": "save",
            "revision": before["revision"],
            "config": {"prompt": "Describe the house."},
        },
    )
    assert saved["valid"]
    assert dict(subentry.data) == {**original, "prompt": "Describe the house."}
    await hass.async_block_till_done()
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Before transfer")])
    result = await conversation.async_converse(
        hass=hass,
        text="Hello",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(result) == "Before transfer"
    for key in ("temperature", "top_p"):
        assert (key in wire.requests[0]["body"]) is explicit_sampling
    exported = await async_management_command(
        hass, owner.id, True, {**base, "action": "export"}
    )
    assert "reasoning_effort" not in exported["document"]["config"]
    imported = await async_management_command(
        hass,
        owner.id,
        True,
        {
            **base,
            "action": "import",
            "mode": "current",
            "document": exported["document"],
            "confirm": True,
            "revision": saved["revision"],
        },
    )
    assert imported["status"] == "updated"
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert "reasoning_effort" not in agent.subentry.data
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("After transfer")])
    result = await conversation.async_converse(
        hass=hass,
        text="Hello again",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(result) == "After transfer"
    for key in ("temperature", "top_p"):
        assert (
            agent.subentry.data[key] == wire.requests[0]["body"][key] == defaults[key]
        )
