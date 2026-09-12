"""Real-HA acceptance coverage for local voice broadcast delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.const import SERVICE_PROCESS
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    async_get_intercom,
)
from tests_real_ha.test_local_intent_exclusions import _make_entry, _setup_entry
from tests_real_ha.test_service_registry_acceptance import (
    _conversation_entity_id,
    _response_service_call,
)


@pytest.mark.asyncio
async def test_real_ha_voice_broadcast_waits_for_idle_target_and_skips_origin(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local voice broadcast must queue while busy, then announce only to peers."""
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)

    entry = _make_entry()
    await _setup_entry(hass, entry)
    entity_id = _conversation_entity_id(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    provider_path = AsyncMock(
        side_effect=AssertionError(
            "Targeted local broadcast unexpectedly fell through to the provider"
        )
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)

    hass.states.async_set(
        "assist_satellite.kitchen",
        "idle",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )
    hass.states.async_set(
        "assist_satellite.hall",
        "responding",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )

    announce_calls: list[dict] = []

    async def announce(call) -> None:
        announce_calls.append(dict(call.data))

    hass.services.async_register("assist_satellite", "announce", announce)

    response = await _response_service_call(
        hass,
        SERVICE_PROCESS,
        {
            "text": "broadcast to everyone dinner is ready",
            "agent_id": entity_id,
            "satellite_id": "assist_satellite.kitchen",
            "language": "en",
        },
    )
    await hass.async_block_till_done()

    assert response["response"] == "Broadcast queued."
    assert response["handled_locally"] is True
    provider_path.assert_not_awaited()
    assert announce_calls == []

    queued = manager.history()
    assert len(queued) == 1
    assert queued[0]["message"] == "dinner is ready"
    assert queued[0]["source"] == "local_voice"
    assert queued[0]["origin_entity_id"] == "assist_satellite.kitchen"
    assert queued[0]["targets"] == ["assist_satellite.hall"]
    assert queued[0]["deliveries"]["assist_satellite.hall"]["status"] == "queued_busy"

    hass.states.async_set(
        "assist_satellite.hall",
        "idle",
        {ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE},
    )
    await hass.async_block_till_done()

    assert announce_calls == [
        {
            "message": "dinner is ready",
            "entity_id": ["assist_satellite.hall"],
        }
    ]
    delivered = manager.history()[0]
    assert delivered["deliveries"]["assist_satellite.hall"]["status"] == "delivered"
