"""Real-HA regression for cleanup after a late config-entry setup failure."""

from __future__ import annotations

from typing import Any

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _registry_entries,
)

_SENSOR_SUFFIXES = {
    "usage",
    "usage_today",
    "usage_month",
    "last_response_usage",
    "guest_mode",
}


def _expected_sensor_unique_ids(subentry_id: str) -> set[str]:
    return {f"{subentry_id}_{suffix}" for suffix in _SENSOR_SUFFIXES}


def _assert_single_clean_platform_set(
    hass: HomeAssistant,
    entry: Any,
    *,
    expected_entity_ids: set[str],
) -> None:
    """Assert retry reused the original registry rows without duplicates."""
    rows = _registry_entries(hass, entry)
    assert {row.entity_id for row in rows} == expected_entity_ids
    assert len(rows) == len(expected_entity_ids)

    conversation_subentry = _conversation_subentry(entry)
    conversation_rows = [row for row in rows if row.domain == "conversation"]
    ai_task_rows = [row for row in rows if row.domain == "ai_task"]
    sensor_rows = [row for row in rows if row.domain == "sensor"]

    assert len(conversation_rows) == 1
    assert len(ai_task_rows) == 1
    assert len(sensor_rows) == len(_SENSOR_SUFFIXES)
    assert conversation_rows[0].config_subentry_id == conversation_subentry.subentry_id
    assert {row.unique_id for row in sensor_rows} == _expected_sensor_unique_ids(
        conversation_subentry.subentry_id
    )
    assert all(
        row.config_subentry_id == conversation_subentry.subentry_id
        for row in sensor_rows
    )


@pytest.mark.asyncio
async def test_late_setup_failure_unloads_registered_platforms_and_retry_is_clean(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late setup failure must not leave stale agents or duplicate/orphaned entities."""
    entry = _make_entry("Partial Setup Retry")
    entry.add_to_hass(hass)

    original_setup_templates = integration.async_setup_templates
    failure_snapshot: dict[str, Any] = {}
    template_setup_calls = 0

    async def fail_once_after_platforms(
        setup_hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        nonlocal template_setup_calls
        template_setup_calls += 1
        assert setup_hass is hass
        assert entry_id == entry.entry_id

        if template_setup_calls > 1:
            await original_setup_templates(setup_hass, entry_id)
            return

        # async_setup_templates runs only after async_forward_entry_setups has
        # completed. Capture the real HA registrations before deliberately failing
        # the remainder of config-entry setup.
        registered_agent = conversation.async_get_agent(hass, entry.entry_id)
        assert registered_agent is not None

        rows = _registry_entries(hass, entry)
        conversation_subentry = _conversation_subentry(entry)
        sensor_rows = [row for row in rows if row.domain == "sensor"]
        assert len([row for row in rows if row.domain == "conversation"]) == 1
        assert len([row for row in rows if row.domain == "ai_task"]) == 1
        assert {row.unique_id for row in sensor_rows} == _expected_sensor_unique_ids(
            conversation_subentry.subentry_id
        )

        guest_mode_row = next(
            row
            for row in sensor_rows
            if row.unique_id == f"{conversation_subentry.subentry_id}_guest_mode"
        )
        guest_mode_state = hass.states.get(guest_mode_row.entity_id)
        assert guest_mode_state is not None
        assert guest_mode_state.state != STATE_UNAVAILABLE

        failure_snapshot.update(
            {
                "agent": registered_agent,
                "entity_ids": {row.entity_id for row in rows},
                "guest_mode_entity_id": guest_mode_row.entity_id,
            }
        )
        raise RuntimeError("intentional late setup failure")

    monkeypatch.setattr(integration, "async_setup_templates", fail_once_after_platforms)

    # Home Assistant owns the setup transaction. The integration should unwind all
    # forwarded platforms when the later template stage fails.
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert template_setup_calls == 1
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert failure_snapshot
    assert conversation.async_get_agent(hass, entry.entry_id) is None

    rows_after_failure = _registry_entries(hass, entry)
    assert {row.entity_id for row in rows_after_failure} == failure_snapshot["entity_ids"]
    assert len(rows_after_failure) == len(failure_snapshot["entity_ids"])

    # Registry rows legitimately survive an unload/failure, but enabled state
    # entities must no longer be live. This catches orphaned sensor entities/listeners.
    guest_mode_state = hass.states.get(failure_snapshot["guest_mode_entity_id"])
    assert guest_mode_state is not None
    assert guest_mode_state.state == STATE_UNAVAILABLE

    # A failed setup leaves the entry in SETUP_ERROR. Home Assistant deliberately
    # rejects another direct async_setup() from that state; async_reload() is the
    # supported public recovery path and performs a fresh setup attempt.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert template_setup_calls == 2
    assert entry.state is ConfigEntryState.LOADED

    recovered_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert recovered_agent is not None
    assert recovered_agent is not failure_snapshot["agent"]

    _assert_single_clean_platform_set(
        hass,
        entry,
        expected_entity_ids=failure_snapshot["entity_ids"],
    )

    recovered_guest_mode = hass.states.get(failure_snapshot["guest_mode_entity_id"])
    assert recovered_guest_mode is not None
    assert recovered_guest_mode.state != STATE_UNAVAILABLE

    # No duplicate platform registration may be hiding behind the same public agent.
    assert len(
        [
            row
            for row in _registry_entries(hass, entry)
            if row.config_entry_id == entry.entry_id
        ]
    ) == len(failure_snapshot["entity_ids"])
    assert DOMAIN in hass.data
