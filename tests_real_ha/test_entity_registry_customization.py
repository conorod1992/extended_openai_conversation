"""Real-HA acceptance for user entity-registry customizations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _registry_entries,
    _setup_entry,
)


@pytest.mark.asyncio
async def test_conversation_registry_customization_survives_reload_and_service_resolution(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed conversation entity remains unique, customized, and callable."""
    entry = _make_entry(
        "Registry Customization",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, entry)

    registry = er.async_get(hass)
    conversation_subentry = _conversation_subentry(entry)
    original_row = next(
        row for row in _registry_entries(hass, entry) if row.domain == "conversation"
    )
    original_entity_id = original_row.entity_id
    original_unique_id = original_row.unique_id
    custom_entity_id = "conversation.custom_extended_openai"
    custom_name = "Custom Extended OpenAI Agent"

    customized = registry.async_update_entity(
        original_entity_id,
        new_entity_id=custom_entity_id,
        name=custom_name,
    )
    await hass.async_block_till_done()

    assert customized.entity_id == custom_entity_id
    assert customized.name == custom_name
    assert registry.async_get(original_entity_id) is None
    assert registry.async_get(custom_entity_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    rows_after = _registry_entries(hass, entry)
    conversation_rows = [row for row in rows_after if row.domain == "conversation"]
    assert len(conversation_rows) == 1
    reloaded_row = conversation_rows[0]
    assert reloaded_row.entity_id == custom_entity_id
    assert reloaded_row.unique_id == original_unique_id
    assert reloaded_row.name == custom_name
    assert reloaded_row.config_subentry_id == conversation_subentry.subentry_id
    assert registry.async_get(original_entity_id) is None

    agent = conversation.async_get_agent(hass, custom_entity_id)
    assert agent is not None
    provider_path = AsyncMock(
        side_effect=AssertionError(
            "A built-in local intent unexpectedly fell through to the provider path"
        )
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_PROCESS,
        {
            "text": "what time is it",
            "agent_id": custom_entity_id,
        },
        blocking=True,
        return_response=True,
    )

    provider_path.assert_not_awaited()
    assert response is not None
    assert response["response"]
    assert response["conversation_id"] is not None
