"""Real Home Assistant acceptance tests for management history boundaries."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from homeassistant.core import HomeAssistant
from tests_real_ha.test_management_backend_acceptance import (
    ADMIN_ID,
    _admin_client,
    _entry,
    _management_call,
    _setup_entry,
)


@pytest.mark.asyncio
async def test_history_command_empty_returns_stable_empty_payload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """The registered management WebSocket exposes bounded history payloads."""
    entry = _entry("Empty History")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    conversations = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=5,
        offset=0,
    )
    assert conversations == {
        "sessions": [],
        "offset": 0,
        "limit": 5,
        "returned": 0,
        "has_more": False,
        "next_offset": None,
        "total": 0,
    }

    usage = await _management_call(
        client,
        entry=entry,
        section="usage",
        action="runs",
        limit=5,
        offset=0,
    )
    assert usage == {
        "runs": [],
        "offset": 0,
        "limit": 5,
        "returned": 0,
        "has_more": False,
        "next_offset": None,
    }


async def test_owned_dispatcher_survives_setup_reload_and_retains_temporary_counts(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Real startup/reload keeps one owned dispatcher and preserves scoped counts."""
    from datetime import timedelta

    from custom_components.extended_openai_conversation_responses.temporary_memory import (
        async_get_temporary_memory,
    )
    from homeassistant.util import dt as dt_util

    command = management_ui.async_management_command
    handlers = management_ui._MANAGEMENT_SECTION_HANDLERS
    entry = _entry("Owned Management API")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    manager = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    await manager.async_add(
        "conversation:temporary-count-regression",
        "A parcel is due this afternoon.",
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        owner_scope_id=f"user:{ADMIN_ID}",
    )

    for reloaded in (False, True):
        if reloaded:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        assert management_ui.async_management_command is command
        assert management_ui._MANAGEMENT_SECTION_HANDLERS is handlers
        assert not hasattr(command, "__wrapped__")

        catalog = await _management_call(
            client,
            entry=entry,
            section="scopes",
            action="catalog",
        )
        own_scope = next(
            scope
            for scope in catalog["scopes"]
            if scope["scope_id"] == f"user:{ADMIN_ID}"
        )
        assert own_scope["temporary_memory_count"] == 1
