"""Real-HA acceptance coverage for ExtendedOpenAI local intent exclusions."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENT_EXCLUSIONS,
    CONF_LOCAL_INTENTS_ENABLED,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent as ha_intent


def _subentry(data: dict) -> dict:
    """Return storage-shaped conversation subentry data."""
    return {
        "data": data,
        "subentry_type": "conversation",
        "title": "Local intent acceptance",
        "unique_id": None,
    }


def _make_entry(*, exclusions: list[str] | None = None) -> MockConfigEntry:
    """Create a real-HA entry with ExtendedOpenAI local intent handling enabled."""
    data = {CONF_LOCAL_INTENTS_ENABLED: True}
    if exclusions:
        data[CONF_LOCAL_INTENT_EXCLUSIONS] = exclusions

    return MockConfigEntry(
        domain=DOMAIN,
        title="Local intent acceptance",
        data={
            CONF_API_KEY: "sk-acceptance-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[_subentry(data)],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry through Home Assistant's config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_real_ha_allowed_local_intent_stays_provider_free(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowed intent is recognized and handled by HA inside our agent."""
    entry = _make_entry()
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    provider_path = AsyncMock(
        side_effect=AssertionError(
            "Allowed local intent unexpectedly fell through to the provider path"
        )
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )

    provider_path.assert_not_awaited()
    assert result.response.as_dict()["speech"]["plain"]["speech"]


@pytest.mark.asyncio
async def test_real_ha_excluded_local_intent_falls_through_to_provider(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same real HA match is rejected when its intent is excluded."""
    entry = _make_entry(exclusions=["HassGetCurrentTime"])
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    async def provider_fallback(user_input, chat_log, request_options):
        response = ha_intent.IntentResponse(language=user_input.language)
        response.async_set_speech("Provider fallback reached")
        return conversation.ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id or "provider-fallback",
        )

    provider_path = AsyncMock(side_effect=provider_fallback)
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )

    provider_path.assert_awaited_once()
    assert (
        result.response.as_dict()["speech"]["plain"]["speech"]
        == "Provider fallback reached"
    )
