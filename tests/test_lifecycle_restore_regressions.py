"""Regressions for lifecycle, deletion and restore ownership."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    intercom,
    model_lifecycle,
    quiet_hours,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryStore,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("subsystem", ["temporary_memory", "usage"])
async def test_detached_pruning_takes_gate_before_manager_lock(
    hass, monkeypatch, subsystem
):
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    if subsystem == "temporary_memory":
        store = TemporaryMemoryStore(hass, 1, "test").bind_agent("entry", "agent")
        manager = TemporaryMemory(store)
        monkeypatch.setattr(manager, "_async_initialize_locked", AsyncMock())
        monkeypatch.setattr(manager, "_async_save_locked", AsyncMock())
        schedule = manager._schedule_pruned_state_save
        task_attribute = "_prune_save_task"
    else:
        store = RecoveryGuardedStore(hass, 1, "test").bind_agent("entry", "agent")
        manager = UsageManager(store)
        monkeypatch.setattr(manager, "_async_persist_detail_state", AsyncMock())
        schedule = manager._async_prune_usage_if_due
        task_attribute = "_prune_task"
    await manager._lock.acquire()
    if subsystem == "usage":
        await schedule()
    else:
        schedule()
    await asyncio.sleep(0)
    # If pruning owned its manager lock before the gate, restore could deadlock.
    assert gate._active_readers == 1
    entered = asyncio.Event()

    async def restore():
        async with gate.exclusive(), manager._lock:
            entered.set()

    writer = asyncio.create_task(restore())
    await asyncio.sleep(0)
    assert not entered.is_set()
    manager._lock.release()
    await asyncio.wait_for(asyncio.gather(getattr(manager, task_attribute), writer), 1)
    assert entered.is_set()


async def test_agent_deletion_erases_only_exact_private_store_prefixes(
    hass, tmp_path, monkeypatch
):
    root = tmp_path / ".storage"
    root.mkdir()
    removed = [
        f"{DOMAIN}.{section}.entry.agent{suffix}"
        for section, suffix in [
            ("memory", ""),
            ("memory", ".embeddings"),
            ("archive", ".metadata"),
            ("archive", ".turns.2026-09"),
            ("usage", ".details"),
            ("knowledge", ""),
            ("guest_mode", ""),
            ("request_rules", ""),
            ("temporary_memory", ""),
        ]
    ]
    retained = [
        f"{DOMAIN}.memory.entry.agent_other",
        f"{DOMAIN}.memory.other.agent",
        f"{DOMAIN}.broadcast",
        "unrelated",
    ]
    for name in removed + retained:
        (root / name).write_text("private", encoding="utf-8")
    monkeypatch.setattr(
        agent_deletion, "clear_retirement_failure", Mock(), raising=False
    )
    monkeypatch.setattr(model_lifecycle, "clear_retirement_failure", Mock())
    from homeassistant.helpers.storage import Store

    async def remove(store):
        Path(store.path).unlink(missing_ok=True)

    monkeypatch.setattr(Store, "async_remove", remove)
    await agent_deletion.async_delete_agent_data(hass, "entry", "agent")
    assert sorted(path.name for path in root.iterdir()) == sorted(retained)
    with pytest.raises(HomeAssistantError, match="deleted"):
        async with get_agent_maintenance_gate(hass, "entry", "agent").shared():
            pytest.fail("A detached writer was admitted after deletion")


async def test_removed_subentry_remains_known_until_cleanup_succeeds(hass, monkeypatch):
    entry = SimpleNamespace(entry_id="entry", subentries={"kept": object()})
    hass.data[agent_deletion.KNOWN_AGENTS] = {"entry": {"kept", "removed"}}
    delete = AsyncMock(side_effect=OSError("storage unavailable"))
    monkeypatch.setattr(agent_deletion, "async_delete_agent_data", delete)
    with pytest.raises(OSError):
        await agent_deletion.async_delete_removed_subentries(hass, entry)
    assert "removed" in hass.data[agent_deletion.KNOWN_AGENTS]["entry"]
    delete.side_effect = None
    await agent_deletion.async_delete_removed_subentries(hass, entry)
    assert hass.data[agent_deletion.KNOWN_AGENTS]["entry"] == {"kept"}


async def test_restore_reload_is_deferred_until_exclusive_lease_released(hass):
    entry = SimpleNamespace(entry_id="entry", state=ConfigEntryState.LOADED)
    subentry = SimpleNamespace(subentry_id="agent")
    hass.data[f"{DOMAIN}.restore_reload_pending"] = {"entry"}
    hass.config_entries.async_reload = AsyncMock(return_value=True)
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    async with gate.exclusive():
        await restore_recovery.async_finish_restore_reload(hass, entry, subentry)
        hass.config_entries.async_reload.assert_not_awaited()
    await restore_recovery.async_finish_restore_reload(hass, entry, subentry)
    hass.config_entries.async_reload.assert_awaited_once_with("entry")
    assert not hass.data[f"{DOMAIN}.restore_reload_pending"]


async def test_last_entry_removal_stops_global_managers_and_blocks_recreation(
    hass, monkeypatch
):
    entry = SimpleNamespace(entry_id="entry", subentries={})
    monkeypatch.setattr(agent_deletion, "async_delete_entry_data", AsyncMock())
    hass.config_entries.async_entries.return_value = []
    qh = SimpleNamespace(async_shutdown=AsyncMock())
    broadcast = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data[DOMAIN] = {"quiet_hours_manager": qh}
    hass.data[intercom.DATA_KEY] = broadcast
    await integration.async_remove_entry(hass, entry)
    qh.async_shutdown.assert_awaited_once()
    broadcast.async_shutdown.assert_awaited_once()
    with pytest.raises(HomeAssistantError, match="removed"):
        await intercom.async_get_intercom(hass)
    with pytest.raises(HomeAssistantError, match="removed"):
        await quiet_hours.async_get_quiet_hours(hass)
    assert hass.services.async_remove.call_count == 3


async def test_ai_task_is_included_in_model_lifecycle_sync(hass, monkeypatch):
    subentry = SimpleNamespace(
        subentry_id="task",
        subentry_type="ai_task_data",
        title="Task",
        data={"chat_model": "gpt-4o"},
    )
    entry = SimpleNamespace(entry_id="entry", subentries={"task": subentry})
    log = Mock()
    monkeypatch.setattr(model_lifecycle, "log_deprecation_once", log)
    monkeypatch.setattr(
        model_lifecycle.ir,
        "async_get",
        lambda _: SimpleNamespace(async_get_issue=lambda *_: None),
    )
    model_lifecycle.sync_entry_model_lifecycle(hass, entry)
    log.assert_called_once()
    assert log.call_args.kwargs["subentry_id"] == "task"


@pytest.mark.parametrize("sections", [{}, {"sections": None}])
async def test_omitted_restore_sections_reuses_preview_selection(
    hass, monkeypatch, sections
):
    from custom_components.extended_openai_conversation_responses import (
        backup_transfer,
        transfer,
    )

    session = backup_transfer.ImportSession(
        session_id="session",
        entry_id="entry",
        subentry_id="agent",
        path="unused",
        filename="backup.json",
        expected_size=1,
        received=1,
        kind="legacy_json",
        expires_at=float("inf"),
        preview_token="token",
        preview_revision="revision",
        preview_sections=("memory",),
    )
    backup_transfer._imports(hass)["session"] = session
    backup_transfer._latest_previews(hass)[("entry", "agent")] = ("session", "token")
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())
    monkeypatch.setattr(
        backup_transfer,
        "_async_load_prepared_restore",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=object()))
    monkeypatch.setattr(backup_transfer, "_snapshot_revision", lambda _: "revision")
    monkeypatch.setattr(backup_transfer, "_async_remove_path", AsyncMock())

    async def restore(*args, sections, precondition):
        assert sections == ("memory",)
        await precondition()
        return {"status": "restored"}

    monkeypatch.setattr(transfer, "async_restore_transfer", restore)
    result = await backup_transfer._restore_import(
        hass,
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
        {"session_id": "session", "preview_token": "token", **sections},
    )
    assert result["status"] == "restored"


async def test_broadcast_shutdown_cancels_drains_and_timers(hass):
    manager = intercom.IntercomManager(hass)
    listener = Mock()
    timer = Mock()
    manager._unsub_state = listener
    manager._expiry_unsubscribers.add(timer)
    manager._enabled = True
    task = asyncio.create_task(asyncio.sleep(3600))
    manager._drain_tasks.add(task)
    await manager.async_shutdown()
    assert task.cancelled()
    listener.assert_called_once()
    timer.assert_called_once()
    assert not manager._queues
    assert not manager._drain_tasks
    with pytest.raises(HomeAssistantError):
        await manager.async_send("should not send", whole_home=True)


async def test_last_entry_cleanup_continues_after_quiet_hours_restore_failure(
    hass, monkeypatch
):
    entry = SimpleNamespace(entry_id="entry", subentries={})
    monkeypatch.setattr(agent_deletion, "async_delete_entry_data", AsyncMock())
    hass.config_entries.async_entries.return_value = []
    qh = SimpleNamespace(
        async_shutdown=AsyncMock(side_effect=HomeAssistantError("save failed"))
    )
    broadcast = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data[DOMAIN] = {"quiet_hours_manager": qh}
    hass.data[intercom.DATA_KEY] = broadcast
    with pytest.raises(HomeAssistantError, match="save failed"):
        await integration.async_remove_entry(hass, entry)
    broadcast.async_shutdown.assert_awaited_once()
    assert hass.services.async_remove.call_count == 3
    assert intercom.DATA_KEY not in hass.data
    assert "quiet_hours_manager" not in hass.data[DOMAIN]


async def test_usage_shutdown_keeps_maintenance_lease_across_both_flushes(
    hass, monkeypatch
):
    store = RecoveryGuardedStore(hass, 1, "test").bind_agent("entry", "agent")
    manager = UsageManager(store)
    gate = store._recovery_gate
    entered = asyncio.Event()
    release = asyncio.Event()
    deleted = asyncio.Event()

    async def aggregates():
        entered.set()
        await release.wait()

    details = AsyncMock()
    monkeypatch.setattr(manager, "_async_save_aggregates", aggregates)
    monkeypatch.setattr(manager, "_async_save_details", details)
    shutdown = asyncio.create_task(manager.async_shutdown())
    await entered.wait()

    async def delete():
        async with gate.exclusive():
            gate.deleted = True
            deleted.set()

    deletion = asyncio.create_task(delete())
    await asyncio.sleep(0)
    assert not deleted.is_set()
    release.set()
    await asyncio.wait_for(asyncio.gather(shutdown, deletion), 1)
    details.assert_awaited_once()
    await manager.async_shutdown()
    details.assert_awaited_once()


@pytest.mark.parametrize("change", [None, "model", "title"])
async def test_restore_reload_decision_uses_effective_configuration(
    hass, monkeypatch, change
):
    from custom_components.extended_openai_conversation_responses import backup

    subentry = SimpleNamespace(
        subentry_id="agent", title="Assistant", data={"memory_mode": "manual"}
    )
    entry = SimpleNamespace(entry_id="entry")
    config = backup.recoverable_configuration_snapshot(subentry.data)
    if change == "model":
        config["chat_model"] = "gpt-4o"
    title = "Restored Assistant" if change == "title" else "Assistant"
    prepared = SimpleNamespace(config=config, title=title)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    await restore_recovery._update_configuration(hass, entry, subentry, prepared)
    assert ("entry" in hass.data.get(f"{DOMAIN}.restore_reload_pending", set())) == (
        change is not None
    )
