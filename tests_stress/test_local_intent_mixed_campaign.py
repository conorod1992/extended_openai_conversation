"""Public Assist local intent routing through repeated live policy changes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENT_EXCLUSIONS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent as ha_intent
from tests_real_ha.test_local_intent_exclusions import _make_entry, _setup_entry
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_local_intent_fallback_remains_exact_after_repeated_policy_reloads(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    entry = _make_entry()
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    turns = 12 * stress_scale
    provider_fallbacks = 0

    for index in range(turns):
        excluded = index % 2 == 1
        if index:
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    **entry.subentries[subentry.subentry_id].data,
                    CONF_LOCAL_INTENT_EXCLUSIONS: ["HassGetCurrentTime"]
                    if excluded
                    else [],
                },
            )
            await hass.async_block_till_done()
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None

        async def provider_fallback(user_input, chat_log, request_options):
            del chat_log, request_options
            response = ha_intent.IntentResponse(language=user_input.language)
            response.async_set_speech("Provider fallback reached")
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id or "local-intent-fallback",
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
        speech = result.response.as_dict()["speech"]["plain"]["speech"]
        if excluded:
            provider_path.assert_awaited_once()
            assert speech == "Provider fallback reached"
            provider_fallbacks += 1
        else:
            provider_path.assert_not_awaited()
            assert speech and speech != "Provider fallback reached"

    record(
        stress_trace,
        "summary",
        layer="real-ha",
        local_intent_turns=turns,
        local_intent_provider_fallbacks=provider_fallbacks,
        local_intent_policy_reloads=turns - 1,
        public_turns=turns,
    )
