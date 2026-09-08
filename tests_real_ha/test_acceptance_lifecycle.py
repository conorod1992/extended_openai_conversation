"""High-value acceptance tests using Home Assistant's real runtime fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_OPTIONS,
    DOMAIN,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENTS_ENABLED,
)
from custom_components.extended_openai_conversation_responses.template import (
    DATA_TEMPLATE_MANAGER,
)


def _subentry(
    subentry_type: str,
    title: str,
    data: dict | None = None,
) -> dict:
    """Return storage-shaped subentry data for MockConfigEntry."""
    return {
        "data": data or {},
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _make_entry(
    title: str = "Acceptance",
    *,
    include_ai_task: bool = True,
    local_intents: bool = False,
) -> MockConfigEntry:
    """Create a current-version entry that cannot make an authentication request."""
    conversation_data = {}
    if local_intents:
        conversation_data[CONF_LOCAL_INTENTS_ENABLED] = True

    subentries = [
        _subentry("conversation", f"{title} Conversation", conversation_data),
    ]
    if include_ai_task:
        subentries.append(
            _subentry(
                "ai_task_data",
                f"{title} AI Task",
                dict(DEFAULT_AI_TASK_OPTIONS),
            )
        )

    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-acceptance-test",
            # The acceptance suite exercises the real HA and integration lifecycle,
            # but must never need an external OpenAI request merely to load an entry.
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=subentries,
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up one entry through Home Assistant's config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _registry_entries(hass: HomeAssistant, entry: MockConfigEntry):
    """Return the real entity-registry rows created for an entry."""
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def _conversation_subentry(entry: MockConfigEntry):
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


@pytest.mark.asyncio
async def test_real_ha_setup_loads_platforms_agent_and_runtime(hass: HomeAssistant) -> None:
    """Set up the assembled integration through HA, not direct platform calls."""
    entry = _make_entry()
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    assert entry.runtime_data is not None
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
    assert DATA_TEMPLATE_MANAGER in hass.data[DOMAIN]

    rows = _registry_entries(hass, entry)
    domains = {row.domain for row in rows}
    assert {"conversation", "ai_task", "sensor"}.issubset(domains)

    conversation_subentry = _conversation_subentry(entry)
    conversation_id = conversation_subentry.subentry_id
    expected_sensor_unique_ids = {
        f"{conversation_id}_usage",
        f"{conversation_id}_usage_today",
        f"{conversation_id}_usage_month",
        f"{conversation_id}_last_response_usage",
        f"{conversation_id}_guest_mode",
    }
    assert expected_sensor_unique_ids.issubset({row.unique_id for row in rows})

    conversation_rows = [row for row in rows if row.domain == "conversation"]
    assert len(conversation_rows) == 1
    assert conversation_rows[0].config_subentry_id == conversation_id

    ai_task_subentry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "ai_task_data"
    )
    ai_task_rows = [row for row in rows if row.domain == "ai_task"]
    assert len(ai_task_rows) == 1
    assert ai_task_rows[0].config_subentry_id == ai_task_subentry.subentry_id

    guest_mode_row = next(
        row for row in rows if row.unique_id == f"{conversation_id}_guest_mode"
    )
    assert hass.states.get(guest_mode_row.entity_id) is not None


@pytest.mark.asyncio
async def test_real_ha_unload_reload_cleans_and_recreates_runtime(
    hass: HomeAssistant,
) -> None:
    """Exercise entity teardown, agent teardown and template ownership via HA."""
    entry = _make_entry(include_ai_task=False)
    await _setup_entry(hass, entry)

    rows_before = _registry_entries(hass, entry)
    entity_ids_before = {row.entity_id for row in rows_before}
    conversation_id = _conversation_subentry(entry).subentry_id
    guest_mode_entity_id = next(
        row.entity_id
        for row in rows_before
        if row.unique_id == f"{conversation_id}_guest_mode"
    )
    template_manager_before = hass.data[DOMAIN][DATA_TEMPLATE_MANAGER]

    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert hass.states.get(guest_mode_entity_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    assert hass.states.get(guest_mode_entity_id) is None
    assert DATA_TEMPLATE_MANAGER not in hass.data.get(DOMAIN, {})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert hass.states.get(guest_mode_entity_id) is not None
    assert hass.data[DOMAIN][DATA_TEMPLATE_MANAGER] is not template_manager_before
    assert {row.entity_id for row in _registry_entries(hass, entry)} == entity_ids_before


@pytest.mark.asyncio
async def test_real_ha_two_entries_do_not_tear_down_each_other(
    hass: HomeAssistant,
) -> None:
    """One entry unloading must not remove another entry's shared runtime."""
    first = _make_entry("First", include_ai_task=False)
    second = _make_entry("Second", include_ai_task=False)
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    manager = hass.data[DOMAIN][DATA_TEMPLATE_MANAGER]
    second_conversation_id = _conversation_subentry(second).subentry_id
    second_guest_mode_entity_id = next(
        row.entity_id
        for row in _registry_entries(hass, second)
        if row.unique_id == f"{second_conversation_id}_guest_mode"
    )

    assert conversation.async_get_agent(hass, first.entry_id) is not None
    assert conversation.async_get_agent(hass, second.entry_id) is not None

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()

    assert conversation.async_get_agent(hass, first.entry_id) is None
    assert conversation.async_get_agent(hass, second.entry_id) is not None
    assert hass.states.get(second_guest_mode_entity_id) is not None
    assert hass.data[DOMAIN][DATA_TEMPLATE_MANAGER] is manager
    assert manager.in_use

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert DATA_TEMPLATE_MANAGER not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_real_ha_legacy_entry_migrates_before_platform_setup(
    hass: HomeAssistant,
) -> None:
    """Exercise the v1-to-current migration inside HA's actual setup lifecycle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy Acceptance",
        data={
            CONF_API_KEY: "sk-acceptance-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        options={"chat_model": "gpt-4o-mini"},
        version=1,
    )

    await _setup_entry(hass, entry)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert dict(entry.options) == {}
    assert {subentry.subentry_type for subentry in entry.subentries.values()} == {
        "conversation",
        "ai_task_data",
    }
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert {"conversation", "ai_task", "sensor"}.issubset(
        {row.domain for row in _registry_entries(hass, entry)}
    )


@pytest.mark.asyncio
async def test_real_ha_public_conversation_api_can_complete_locally(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Join HA's public Conversation API to the integration without provider I/O."""
    entry = _make_entry(include_ai_task=False, local_intents=True)
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    provider_path = AsyncMock(
        side_effect=AssertionError(
            "A built-in local intent unexpectedly fell through to the provider path"
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
    response = result.response.as_dict()
    assert response["speech"]["plain"]["speech"]
    assert result.conversation_id is not None
