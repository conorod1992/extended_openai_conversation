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


@pytest.mark.parametrize("caller", ["restricted", "owner", "voice"])
async def test_local_state_query_respects_real_ha_read_permissions(hass, monkeypatch, caller):
    from homeassistant.auth.models import Group
    from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_READ
    from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from pytest_homeassistant_custom_component.common import MockUser

    user = MockUser(
        is_owner=False,
        groups=[
            Group(
                id="read-boundary",
                name="Read boundary",
                policy={
                    CAT_ENTITIES: {
                        ENTITY_ENTITY_IDS: {"light.allowed": {POLICY_READ: True}}
                    }
                },
            )
        ],
    )
    user.add_to_hass(hass)
    if caller == "owner":
        user = MockUser(is_owner=True)
        user.add_to_hass(hass)
    entry = _make_entry()
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    hass.states.async_set("light.private", "on", {"friendly_name": "Private light"})
    async_expose_entity(hass, "conversation", "light.private", True)

    async def fallback(user_input, chat_log, request_options):
        response = ha_intent.IntentResponse(language="en")
        response.async_set_speech("Permission-filtered fallback")
        return conversation.ConversationResult(
            response=response, conversation_id="permission"
        )

    provider = AsyncMock(side_effect=fallback)
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider)
    result = await conversation.async_converse(
        hass=hass,
        text="is the private light on",
        conversation_id=None,
        context=Context(user_id=None if caller == "voice" else user.id),
        language="en",
        agent_id=entry.entry_id,
    )
    if caller == "restricted":
        provider.assert_awaited_once()
        assert result.response.as_dict()["speech"]["plain"]["speech"] == "Permission-filtered fallback"
    else:
        provider.assert_not_awaited()
        assert result.response.as_dict()["speech"]["plain"]["speech"]


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
