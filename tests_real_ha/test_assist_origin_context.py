"""Real-HA acceptance for Assist device and satellite origin metadata."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


@pytest.mark.asyncio
async def test_public_assist_preserves_real_device_and_satellite_origin_context(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assist origin metadata reaches the loaded agent and HA LLM context unchanged."""
    entry = _make_entry(
        "Assist Origin",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    # Build a genuine HA device/entity relationship representing the Assist satellite
    # that originated the request rather than passing an arbitrary device identifier.
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test_assist_origin", "kitchen-satellite-device")},
        name="Kitchen Assist Satellite",
    )
    entity_registry = er.async_get(hass)
    satellite = entity_registry.async_get_or_create(
        "assist_satellite",
        "test_assist_origin",
        "kitchen-satellite",
        suggested_object_id="kitchen_satellite",
        device_id=device.id,
    )
    assert satellite.device_id == device.id

    provider_path = AsyncMock(
        side_effect=AssertionError("local intent unexpectedly reached the provider")
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    original_process = agent._async_process
    observed: dict[str, Any] = {}

    async def capture_origin(
        user_input: conversation.ConversationInput,
    ) -> conversation.ConversationResult:
        observed["device_id"] = user_input.device_id
        observed["satellite_id"] = user_input.satellite_id
        observed["context"] = user_input.context
        llm_context = user_input.as_llm_context(conversation.DOMAIN)
        observed["llm_device_id"] = llm_context.device_id
        observed["llm_context"] = llm_context.context
        return await original_process(user_input)

    monkeypatch.setattr(agent, "_async_process", capture_origin)

    request_context = Context(user_id="assist-origin-user")
    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=request_context,
        language="en",
        agent_id=entry.entry_id,
        device_id=device.id,
        satellite_id=satellite.entity_id,
    )

    assert result.response.error_code is None
    provider_path.assert_not_awaited()
    assert observed == {
        "device_id": device.id,
        "satellite_id": satellite.entity_id,
        "context": request_context,
        "llm_device_id": device.id,
        "llm_context": request_context,
    }

    # The origin relationship itself must still be the real registry relationship
    # after the request; processing must not rewrite or detach the satellite entity.
    restored_satellite = entity_registry.async_get(satellite.entity_id)
    assert restored_satellite is not None
    assert restored_satellite.device_id == device.id
