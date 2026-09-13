"""Real-HA acceptance coverage for multi-entry lifecycle isolation."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.template import (
    DATA_TEMPLATE_MANAGER,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er


def _subentry(title: str) -> dict[str, Any]:
    """Return one storage-shaped conversation subentry."""
    return {
        "data": {},
        "subentry_type": "conversation",
        "title": f"{title} Conversation",
        "unique_id": None,
    }


def _make_entry(title: str) -> MockConfigEntry:
    """Create a current-version entry that never requires provider I/O to load."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-multi-entry-acceptance-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[_subentry(title)],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry through Home Assistant's real config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry_id(entry: MockConfigEntry) -> str:
    """Return the sole conversation subentry id for an acceptance entry."""
    return next(
        subentry.subentry_id
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


def _registry_entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return entity-registry ids owned by one config entry."""
    registry = er.async_get(hass)
    return {
        row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def _guest_mode_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the guest-mode sensor entity id for an entry."""
    conversation_id = _conversation_subentry_id(entry)
    registry = er.async_get(hass)
    return next(
        row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
        if row.unique_id == f"{conversation_id}_guest_mode"
    )


def _state_value(hass: HomeAssistant, entity_id: str) -> str:
    """Return a state value through Home Assistant's StateMachine API."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _integration_tasks() -> Counter[tuple[str, str]]:
    """Snapshot pending asyncio tasks whose coroutine originates in this integration."""
    current = asyncio.current_task()
    tasks: Counter[tuple[str, str]] = Counter()
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        coro: Coroutine[Any, Any, Any] = task.get_coro()
        code = getattr(coro, "cr_code", None) or getattr(coro, "ag_code", None)
        filename = getattr(code, "co_filename", "")
        if "extended_openai_conversation_responses" not in filename:
            continue
        tasks[(Path(filename).name, getattr(code, "co_name", type(coro).__name__))] += 1
    return tasks


async def _assert_agent_can_answer(
    hass: HomeAssistant,
    agent: ExtendedOpenAIAgentEntity,
    *,
    text: str,
    reply: str,
) -> None:
    """Exercise one loaded agent through Home Assistant's public Conversation API."""

    async def model(log, **kwargs):
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=agent.entity_id, content=reply)
        )
        return None

    original = agent._async_handle_chat_log
    agent._async_handle_chat_log = model
    try:
        result = await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )
    finally:
        agent._async_handle_chat_log = original

    assert result.response.as_dict()["speech"]["plain"]["speech"] == reply


@pytest.mark.asyncio
async def test_real_ha_multi_entry_reload_isolated_without_lifecycle_leaks(
    hass: HomeAssistant,
) -> None:
    """Repeatedly reload one entry without disturbing or leaking into another."""
    first = _make_entry("First")
    second = _make_entry("Second")
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    first_agent = conversation.async_get_agent(hass, first.entry_id)
    second_agent = conversation.async_get_agent(hass, second.entry_id)
    assert isinstance(first_agent, ExtendedOpenAIAgentEntity)
    assert isinstance(second_agent, ExtendedOpenAIAgentEntity)

    first_entity_ids = _registry_entity_ids(hass, first)
    second_entity_ids = _registry_entity_ids(hass, second)
    first_guest_mode = _guest_mode_entity_id(hass, first)
    second_guest_mode = _guest_mode_entity_id(hass, second)

    manager = hass.data[DOMAIN][DATA_TEMPLATE_MANAGER]
    manager_stop_listener = manager._remove_stop_listener
    assert manager.in_use
    assert manager._entry_ids == {first.entry_id, second.entry_id}
    assert manager_stop_listener is not None
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    baseline_tasks = _integration_tasks()

    await _assert_agent_can_answer(
        hass, second_agent, text="second before reloads", reply="second-ready"
    )

    previous_first_agent = first_agent
    for cycle in range(3):
        assert await hass.config_entries.async_unload(first.entry_id)
        await hass.async_block_till_done()

        assert first.state is ConfigEntryState.NOT_LOADED
        assert conversation.async_get_agent(hass, first.entry_id) is None
        assert conversation.async_get_agent(hass, second.entry_id) is second_agent
        assert manager._entry_ids == {second.entry_id}
        assert hass.data[DOMAIN][DATA_TEMPLATE_MANAGER] is manager
        assert manager._remove_stop_listener is manager_stop_listener
        assert manager.in_use
        assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
        assert _state_value(hass, first_guest_mode) == STATE_UNAVAILABLE
        assert _state_value(hass, second_guest_mode) != STATE_UNAVAILABLE
        assert _registry_entity_ids(hass, second) == second_entity_ids

        await _assert_agent_can_answer(
            hass,
            second_agent,
            text=f"second while first unloaded {cycle}",
            reply=f"second-still-ready-{cycle}",
        )

        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

        reloaded_first_agent = conversation.async_get_agent(hass, first.entry_id)
        assert isinstance(reloaded_first_agent, ExtendedOpenAIAgentEntity)
        assert reloaded_first_agent is not previous_first_agent
        assert conversation.async_get_agent(hass, second.entry_id) is second_agent
        assert first.state is ConfigEntryState.LOADED
        assert second.state is ConfigEntryState.LOADED
        assert hass.data[DOMAIN][DATA_TEMPLATE_MANAGER] is manager
        assert manager._entry_ids == {first.entry_id, second.entry_id}
        assert manager._remove_stop_listener is manager_stop_listener
        assert _registry_entity_ids(hass, first) == first_entity_ids
        assert _registry_entity_ids(hass, second) == second_entity_ids
        assert _state_value(hass, first_guest_mode) != STATE_UNAVAILABLE
        assert _state_value(hass, second_guest_mode) != STATE_UNAVAILABLE

        # Completed unload/reload cycles must not accumulate integration-owned tasks.
        assert _integration_tasks() == baseline_tasks

        await _assert_agent_can_answer(
            hass,
            reloaded_first_agent,
            text=f"first after reload {cycle}",
            reply=f"first-ready-{cycle}",
        )
        await _assert_agent_can_answer(
            hass,
            second_agent,
            text=f"second after first reload {cycle}",
            reply=f"second-ready-{cycle}",
        )
        previous_first_agent = reloaded_first_agent

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert manager._entry_ids == {second.entry_id}
    assert conversation.async_get_agent(hass, second.entry_id) is second_agent

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()

    assert first.state is ConfigEntryState.NOT_LOADED
    assert second.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, first.entry_id) is None
    assert conversation.async_get_agent(hass, second.entry_id) is None
    assert DATA_TEMPLATE_MANAGER not in hass.data.get(DOMAIN, {})
    assert not manager.in_use
    assert manager._entry_ids == set()
    assert manager._remove_stop_listener is None
    assert manager._original_init is None
    assert manager._replacement_init is None
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
    assert _state_value(hass, first_guest_mode) == STATE_UNAVAILABLE
    assert _state_value(hass, second_guest_mode) == STATE_UNAVAILABLE

    # Final teardown may remove legitimate entry-owned tasks; it must never leave
    # additional integration tasks that were not present in the stable loaded state.
    assert not (_integration_tasks() - baseline_tasks)
