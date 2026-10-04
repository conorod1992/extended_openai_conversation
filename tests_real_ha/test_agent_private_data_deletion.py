"""Real HA deletion removes private stores and global device effects."""
from pathlib import Path

# This regression verifies actual private files and native Store cleanup.
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401

from homeassistant.helpers.storage import Store

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses import intercom, quiet_hours
from tests_real_ha.test_live_subentry_removal import _entry, _setup_entry, _subentry_by_title


async def test_subentry_removal_purges_private_stores_then_last_entry_stops_globals(hass, real_store_io):
    entry = _entry()
    await _setup_entry(hass, entry)
    removed = _subentry_by_title(entry, "Conversation A")
    remaining = _subentry_by_title(entry, "Conversation B")
    sections = ("memory", "temporary_memory", "knowledge", "archive", "usage", "guest_mode", "request_rules")
    owned = []
    for section in sections:
        suffix = ".turns.2026-09" if section == "archive" else ".embeddings" if section == "memory" else ""
        store = Store(hass, 1, f"{DOMAIN}.{section}.{entry.entry_id}.{removed.subentry_id}{suffix}", private=True)
        await store.async_save({"private": "deletion test"})
        owned.append(Path(store.path))
    sibling = Store(hass, 1, f"{DOMAIN}.request_rules.{entry.entry_id}.{remaining.subentry_id}.sentinel", private=True)
    await sibling.async_save({"private": "keep sibling"})
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
