"""Real HA deletion removes private stores and global device effects."""

from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    delayed_tools,
    intercom,
    quiet_hours,
    transfer_staging,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from tests.ha_store_test_helpers import invalidate_store_cache
from tests.test_delayed_tools import _record
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
        ("memory", ""),
        ("memory", ".embeddings"),
        ("temporary_memory", ""),
        ("knowledge", ""),
        ("archive", ".turns.2026-09"),
        ("usage", ""),
        ("guest_mode", ""),
        ("request_rules", ""),
    )
    owned = []
    for section, suffix in sections:
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
    assert all(path.is_file() for path in owned)
    assert Path(sibling.path).is_file()
    broadcast = await intercom.async_get_intercom(hass)
    qh = await quiet_hours.async_get_quiet_hours(hass)
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
    if provider_state != "loaded":
        for getter in (intercom.async_get_intercom, quiet_hours.async_get_quiet_hours):
            with pytest.raises(HomeAssistantError, match="loaded"):
                await getter(hass)
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


@pytest.mark.parametrize("parent_removed", [False, True])
@pytest.mark.parametrize("corrupt_envelope", [False, True])
async def test_malformed_delayed_queue_defers_orphan_cleanup_without_blocking_startup(
    hass, real_store_io, monkeypatch, parent_removed, corrupt_envelope
):
    entry = _entry()
    entry.add_to_hass(hass)
    orphan_parent = "removed-provider" if parent_removed else entry.entry_id
    orphan_id = "deleted-assistant"
    orphan = Store(
        hass, 1, f"{DOMAIN}.usage.{orphan_parent}.{orphan_id}.daily", private=True
    )
    queue = Store(
        hass,
        delayed_tools.DELAYED_TOOL_STORAGE_VERSION,
        delayed_tools.DELAYED_TOOL_STORAGE_KEY,
        private=True,
        atomic_writes=True,
    )
    await orphan.async_save({"days": {}})
    await queue.async_save({"calls": {"malformed": []}})
    queue_file = Path(queue.path)
    if corrupt_envelope:
        await hass.async_add_executor_job(queue_file.write_bytes, b'{"data":{"calls":[]}}')
        invalidate_store_cache(queue)
    original = await hass.async_add_executor_job(queue_file.read_bytes)
    retry_callbacks = []

    def schedule_retry(_hass, _delay, retry):
        retry_callbacks.append(retry)
        return lambda: None

    monkeypatch.setattr(agent_deletion, "async_call_later", schedule_retry)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert "extended-openai" in hass.data[DATA_PANELS]
    assert hass.services.has_service(DOMAIN, "broadcast")
    scheduler = hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER]
    assert scheduler._quarantined and not scheduler._tasks
    assert Path(orphan.path).exists()
    assert retry_callbacks
    await retry_callbacks[-1](None)
    assert Path(orphan.path).exists()
    assert await hass.async_add_executor_job(queue_file.read_bytes) == original

    # A later repaired queue can be cleaned using the retry without rearming it.
    retired = replace(_record(), entry_id=orphan_parent, subentry_id=orphan_id)
    current = replace(
        retired,
        call_id="current",
        entry_id=entry.entry_id,
        subentry_id=_subentry_by_title(entry, "Conversation A").subentry_id,
    )
    await queue.async_save({"calls": [retired.as_dict(), current.as_dict()]})
    await retry_callbacks[-1](None)
    assert not Path(orphan.path).exists()
    assert await queue.async_load() == {"calls": [current.as_dict()]}
    assert scheduler._quarantined and not scheduler._tasks


async def test_undeletable_orphan_does_not_block_setup(hass, real_store_io, monkeypatch):
    entry = _entry()
    entry.add_to_hass(hass)
    orphan = Path(hass.config.path(
        ".storage", f"{DOMAIN}.archive.{entry.entry_id}.deleted-assistant.metadata"
    ))

    def create_directory():
        orphan.parent.mkdir(exist_ok=True)
        orphan.mkdir()

    await hass.async_add_executor_job(create_directory)
    retries = []

    def schedule(_hass, _delay, retry):
        retries.append(retry)
        return lambda: None

    monkeypatch.setattr(agent_deletion, "async_call_later", schedule)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert "extended-openai" in hass.data[DATA_PANELS]
    assert hass.services.has_service(DOMAIN, "broadcast")
    assert orphan.is_dir() and retries

    await hass.async_add_executor_job(orphan.rmdir)
    await retries[-1](None)
    assert "deleted-assistant" not in hass.data[agent_deletion.KNOWN_AGENTS][entry.entry_id]


async def test_staging_failure_keeps_assistants_available_and_retries(hass, monkeypatch):
    entry = _entry()
    entry.add_to_hass(hass)

    def inaccessible(_root):
        raise PermissionError("staging is unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(transfer_staging, "_initialize_owner", inaccessible)
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert "extended-openai" in hass.data[DATA_PANELS]
        assert hass.services.has_service(DOMAIN, "broadcast")
        assert transfer_staging._OWNER_KEY not in hass.data

    directory = await transfer_staging.async_get_transfer_staging(hass)
    assert directory.is_dir()
    await transfer_staging.async_close_transfer_staging(hass)
