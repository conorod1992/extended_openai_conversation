"""Real-HA acceptance for integration-level shared service lifetime."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


def _conversation_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the real conversation entity id registered for one config entry."""
    rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return next(row.entity_id for row in rows if row.domain == "conversation")


async def _process(
    hass: HomeAssistant,
    *,
    agent_id: str | None = None,
) -> dict:
    """Invoke the integration-level process service through Home Assistant."""
    data = {"text": "what time is it"}
    if agent_id is not None:
        data["agent_id"] = agent_id
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_PROCESS,
        data,
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


@pytest.mark.asyncio
async def test_shared_process_service_survives_entry_churn_without_stale_routing(
    hass: HomeAssistant,
) -> None:
    """The integration-level service outlives entries but never routes to stale agents."""
    first = _make_entry(
        "Shared Service First",
        include_ai_task=False,
        local_intents=True,
    )
    second = _make_entry(
        "Shared Service Second",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    first_entity_id = _conversation_entity_id(hass, first)
    second_entity_id = _conversation_entity_id(hass, second)

    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    # With two loaded agents, explicit routing to the second one must work.
    second_response = await _process(hass, agent_id=second_entity_id)
    assert second_response["handled_locally"] is True
    assert second_response["response"]

    # Unloading only the first entry must leave the integration-level service and
    # the second entry fully operational, while the first entity id becomes stale.
    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert first.state is ConfigEntryState.NOT_LOADED
    assert second.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, agent_id=first_entity_id)

    second_response_after_first_unload = await _process(
        hass, agent_id=second_entity_id
    )
    assert second_response_after_first_unload["handled_locally"] is True
    assert second_response_after_first_unload["response"]

    # The final entry unload tears down every agent, but not integration-level
    # services registered from async_setup(). Calls must fail as no-agent routing
    # errors rather than by reaching stale entry runtime or losing the service.
    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert second.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, agent_id=second_entity_id)

    with pytest.raises(
        HomeAssistantError,
        match="No Extended OpenAI conversation agent is available",
    ):
        await _process(hass)

    # Load a fresh entry after the domain has temporarily had zero active config
    # entries. The already-registered shared service must discover only the new
    # live agent, ignoring the persisted registry rows for the unloaded entries.
    third = _make_entry(
        "Shared Service Third",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, third)
    third_entity_id = _conversation_entity_id(hass, third)

    assert third.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
    assert third_entity_id not in {first_entity_id, second_entity_id}

    # No agent_id is important here: process() scans the entity registry, which
    # still contains rows for A and B, and must filter those stale rows by the live
    # Home Assistant conversation-agent registry before selecting C.
    third_response = await _process(hass)
    assert third_response["handled_locally"] is True
    assert third_response["response"]

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, agent_id=first_entity_id)
    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, agent_id=second_entity_id)

    explicit_third_response = await _process(hass, agent_id=third_entity_id)
    assert explicit_third_response["handled_locally"] is True
    assert explicit_third_response["response"]
