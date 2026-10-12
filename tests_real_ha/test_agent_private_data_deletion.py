"""Real HA deletion removes private stores and global device effects."""

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    intercom,
    quiet_hours,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers.storage import Store
from tests_real_ha.test_live_subentry_removal import (
    _entry,
    _setup_entry,
    _subentry_by_title,
)

# This regression verifies actual private files and native Store cleanup.
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401, F811


@pytest.mark.parametrize("provider_state", ["loaded", "unloaded", "disabled"])
async def test_subentry_removal_purges_private_stores_then_last_entry_stops_globals(
    hass, real_store_io, provider_state
):
    entry = _entry()
    await _setup_entry(hass, entry)
    removed = _subentry_by_title(entry, "Conversation A")
    remaining = _subentry_by_title(entry, "Conversation B")
    sections = (
        "memory",
        "temporary_memory",
        "knowledge",
        "archive",
        "usage",
        "guest_mode",
        "request_rules",
    )
    owned = []
    for section in sections:
        suffix = (
            ".turns.2026-09"
            if section == "archive"
            else ".embeddings"
            if section == "memory"
            else ""
        )
        store = Store(
            hass,
            1,
            f"{DOMAIN}.{section}.{entry.entry_id}.{removed.subentry_id}{suffix}",
            private=True,
        )
        await store.async_save({"private": "deletion test"})
        owned.append(Path(store.path))
    sibling = Store(
        hass,
        1,
        f"{DOMAIN}.request_rules.{entry.entry_id}.{remaining.subentry_id}.sentinel",
        private=True,
    )
    await sibling.async_save({"private": "keep sibling"})
    if provider_state == "unloaded":
        assert await hass.config_entries.async_unload(entry.entry_id)
    elif provider_state == "disabled":
        assert await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )
    await hass.async_block_till_done()
    assert hass.config_entries.async_remove_subentry(entry, removed.subentry_id)
    await hass.async_block_till_done()
    assert all(not path.exists() for path in owned)
    assert Path(sibling.path).exists()
    broadcast = await intercom.async_get_intercom(hass)
    qh = await quiet_hours.async_get_quiet_hours(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not Path(sibling.path).exists()
    assert broadcast._closed
    assert not qh._unsubscribers
    assert not hass.services.has_service(DOMAIN, "broadcast")
    assert not hass.services.has_service(DOMAIN, "enable_quiet_hours")
    fresh = _entry()
    await _setup_entry(hass, fresh)
    assert hass.services.has_service(DOMAIN, "broadcast")
    assert hass.services.has_service(DOMAIN, "enable_quiet_hours")


async def test_provider_erasure_failure_is_rediscovered_without_provider_or_tracking(
    hass, real_store_io, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import agent_deletion

    entry = _entry()
    await _setup_entry(hass, entry)
    owner = _subentry_by_title(entry, "Conversation A")
    store = Store(
        hass,
        1,
        f"{DOMAIN}.memory.{entry.entry_id}.{owner.subentry_id}.retry",
        private=True,
    )
    await store.async_save({"private": "must be retried"})
    path = Path(store.path)
    remove = Store.async_remove

    async def fail(selected):
        if selected.path == store.path:
            raise OSError("Transient private-store deletion failure")
        await remove(selected)

    with monkeypatch.context() as scoped:
        scoped.setattr(Store, "async_remove", fail)
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.config_entries.async_get_entry(entry.entry_id) is None
        assert path.exists()
    hass.data.pop(agent_deletion.KNOWN_AGENTS, None)
    await agent_deletion.async_reconcile_agent_storage(hass)
    assert not path.exists()
