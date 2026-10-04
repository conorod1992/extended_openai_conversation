"""Save and effective preview use the same authoritative model/template contract."""

import json

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire


async def test_full_editor_draft_model_switch_previews_and_sends_without_reasoning(
    hass, monkeypatch
):
    agent = await _agent(hass, reasoning_effort="minimal", chat_model="gpt-5-mini")
    owner = await hass.auth.async_create_user(
        "Configuration owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": agent.entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
    }

    async def command(action, **extra):
        return await async_management_command(
            hass, owner.id, True, {**base, "action": action, **extra}
        )

    snapshot = await command("get")
    draft = {
        **snapshot["config"],
        "chat_model": "gpt-4o",
        "api_mode": "chat_completions",
    }
    await command("update", config=draft, revision=snapshot["revision"])
    await hass.async_block_till_done()
    previous_agent = agent
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent is not previous_agent
    assert "reasoning_effort" not in agent.subentry.data
    # The browser can still carry the old model's value even after a sparse Save.
    preview = await command("request_preview", config=draft)
    settings = json.loads(
        next(
            section["content"]
            for section in preview["sections"]
            if section["key"] == "request_settings"
        )
    )
    assert "reasoning_effort" not in settings
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Healthy")])
    result = await conversation.async_converse(
        hass=hass,
        text="Previewed model",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )
    assert result.response.error_code is None
    assert "reasoning_effort" not in wire.requests[0]["body"]


async def test_invalid_system_prompt_save_is_rejected_without_changing_saved_data(hass):
    agent = await _agent(hass)
    owner = await hass.auth.async_create_user(
        "Prompt owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": agent.entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
    }
    snapshot = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    original = dict(agent.subentry.data)
    with pytest.raises(AgentConfigError, match=r"prompt.*invalid template"):
        await async_management_command(
            hass,
            owner.id,
            True,
            {
                **base,
                "action": "update",
                "config": {"prompt": "{{ invalid"},
                "revision": snapshot["revision"],
            },
        )
    assert dict(agent.subentry.data) == original
    valid = "{% set names = states.light | map(attribute='name') | list %}{{ names | join(', ') }}"
    await async_management_command(
        hass,
        owner.id,
        True,
        {
            **base,
            "action": "update",
            "config": {"prompt": valid},
            "revision": snapshot["revision"],
        },
    )
    assert agent.subentry.data["prompt"] == valid
