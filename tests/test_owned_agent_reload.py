"""Started reloads and their reconciliation outlive cancellation of the caller."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    live_subentry_update,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError


async def _cancel_twice_while_pending(task):
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Caller released an unfinished reload"


@pytest.mark.parametrize("outcome", ["success", "rejected", "failed"])
async def test_restore_reload_settles_core_and_marker_before_cancellation(outcome):
    entered, release = asyncio.Event(), asyncio.Event()
    entry = SimpleNamespace(entry_id="entry", state=ConfigEntryState.LOADED)
    subentry = SimpleNamespace(subentry_id="agent")
    pending = {entry.entry_id}

    async def reload(_entry_id):
        entry.state = ConfigEntryState.UNLOAD_IN_PROGRESS
        entered.set()
        await release.wait()
        entry.state = (
            ConfigEntryState.LOADED
            if outcome == "success"
            else ConfigEntryState.SETUP_ERROR
        )
        if outcome == "failed":
            raise OSError("reload unavailable")
        return outcome == "success"

    reload_call = AsyncMock(side_effect=reload)
    hass = SimpleNamespace(
        data={f"{DOMAIN}.restore_reload_pending": pending},
        config_entries=SimpleNamespace(async_reload=reload_call),
    )
    task = asyncio.create_task(
        restore_recovery.async_finish_restore_reload(hass, entry, subentry)
    )
    await entered.wait()
    try:
        await _cancel_twice_while_pending(task)
        assert pending == {entry.entry_id}
        release.set()
        expected = (
            asyncio.CancelledError
            if outcome == "success"
            else HomeAssistantError
            if outcome == "rejected"
            else OSError
        )
        with pytest.raises(expected):
            await task
        reload_call.assert_awaited_once_with(entry.entry_id)
        assert entry.state is not ConfigEntryState.UNLOAD_IN_PROGRESS
        assert pending == (set() if outcome == "success" else {entry.entry_id})
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_listener_settles_reload_and_deletion_before_cancellation(monkeypatch):
    reloading, release_reload, deleting, release_delete = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    events = []
    entry = SimpleNamespace(entry_id="entry")

    async def reload(_entry_id):
        events.append("reload started")
        reloading.set()
        await release_reload.wait()
        events.append("reload finished")
        return True

    async def delete(_hass, _entry):
        events.append("cleanup started")
        deleting.set()
        await release_delete.wait()
        events.append("cleanup finished")

    reload_call = AsyncMock(side_effect=reload)
    cleanup = AsyncMock(side_effect=delete)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_call))
    monkeypatch.setattr(integration, "sync_entry_model_lifecycle", Mock())
    monkeypatch.setattr(agent_deletion, "async_delete_removed_subentries", cleanup)
    task = asyncio.create_task(integration.update_listener(hass, entry))
    await reloading.wait()
    try:
        await _cancel_twice_while_pending(task)
        release_reload.set()
        await deleting.wait()
        assert not task.done()
        await _cancel_twice_while_pending(task)
        release_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == [
            "reload started",
            "reload finished",
            "cleanup started",
            "cleanup finished",
        ]
        reload_call.assert_awaited_once_with(entry.entry_id)
        cleanup.assert_awaited_once_with(hass, entry)
    finally:
        release_reload.set()
        release_delete.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_live_listener_keeps_no_reload_no_settlement_fast_path(monkeypatch):
    reload_call = AsyncMock(side_effect=AssertionError("live update reloaded"))
    cleanup = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_call))
    entry = SimpleNamespace(entry_id="entry")
    monkeypatch.setattr(integration, "sync_entry_model_lifecycle", Mock())
    monkeypatch.setattr(agent_deletion, "async_delete_removed_subentries", cleanup)
    monkeypatch.setattr(
        integration,
        "async_settle_owned_operation",
        Mock(side_effect=AssertionError("live update created a settlement task")),
        raising=False,
    )
    with live_subentry_update():
        await integration.update_listener(hass, entry)
    reload_call.assert_not_awaited()
    cleanup.assert_awaited_once_with(hass, entry)
