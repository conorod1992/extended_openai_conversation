"""Final meaningful cancellation, durable recovery, and archive contracts."""

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive as archives,
    guest_mode,
    management_history_queries as history,
    usage,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    _async_settle_native_edit,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_conversation_archive import FakeArchiveStorage
from tests.test_guest_mode_persistence import _manager as guest_manager
from tests.test_memory import _memory
from tests.test_temporary_memory import Storage, TemporaryMemory, stored_record
from tests.test_usage import FakeStorage


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_archive_query_repeated_cancellation_retains_lock_until_worker_settles(
    worker_fails,
):
    started, release, settled = Event(), Event(), Event()
    archive = SimpleNamespace(_lock=asyncio.Lock(), _ensure_initialized=Mock())

    def query():
        started.set()
        try:
            assert release.wait(5)
            if worker_fails:
                raise OSError("archive query failed")
            return "done"
        finally:
            settled.set()

    owner = asyncio.create_task(history._async_archive_query(archive, query))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        owner.cancel("first cancellation")
        await asyncio.sleep(0)
        owner.cancel("second cancellation")
        await asyncio.sleep(0)
        assert archive._lock.locked() and not owner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="first cancellation"):
            await owner
        assert settled.is_set() and not archive._lock.locked()
        archive._ensure_initialized.assert_called_once()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)


async def test_native_file_edit_repeated_cancellation_waits_for_write_completion():
    started, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def write():
        started.set()
        await release.wait()
        writes.append("durable")
        return 1

    task = asyncio.create_task(_async_settle_native_edit(write()))
    await started.wait()
    task.cancel("first")
    await asyncio.sleep(0)
    task.cancel("second")
    await asyncio.sleep(0)
    assert writes == [] and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="first"):
        await task
    assert writes == ["durable"]


async def test_usage_shutdown_skips_restore_quarantine_without_writing(
    hass, monkeypatch
):
    store = RecoveryGuardedStore(hass, 1, "usage-final").bind_agent("entry", "agent")
    store._recovery_gate.recovery_required = True
    save = AsyncMock()
    monkeypatch.setattr(store, "async_save", save)
    manager = usage.UsageManager(store)
    await manager.async_shutdown()
    save.assert_not_awaited()
    assert not manager._stopping


@pytest.mark.parametrize("guarded", [False, True])
async def test_usage_shutdown_propagates_storage_failure(hass, monkeypatch, guarded):
    store = (
        RecoveryGuardedStore(hass, 1, "usage-final").bind_agent("entry", "agent")
        if guarded
        else FakeStorage()
    )
    manager = usage.UsageManager(store)
    flush = AsyncMock(side_effect=HomeAssistantError("flush unavailable"))
    monkeypatch.setattr(manager, "_async_save_aggregates", flush)
    detail = AsyncMock()
    monkeypatch.setattr(manager, "_async_save_details", detail)
    with pytest.raises(HomeAssistantError, match="flush unavailable"):
        await manager.async_shutdown()
    flush.assert_awaited_once()
    detail.assert_not_awaited()
    assert manager._stopping


async def test_usage_shutdown_ignores_completed_request_owner(monkeypatch):
    manager = usage.UsageManager(FakeStorage())
    completed = asyncio.create_task(asyncio.sleep(0))
    await completed
    manager._active_runs["finished"] = completed
    aggregate, detail = AsyncMock(), AsyncMock()
    monkeypatch.setattr(manager, "_async_save_aggregates", aggregate)
    monkeypatch.setattr(manager, "_async_save_details", detail)
    await manager.async_shutdown()
    assert not completed.cancelled()
    aggregate.assert_awaited_once()
    detail.assert_awaited_once()


@pytest.mark.parametrize(
    "malformed", [{"broken": True}, {"expires_at": "not-a-time"}, None]
)
async def test_temporary_memory_failed_ack_readback_salvages_valid_records(malformed):
    valid = stored_record(1)
    storage = Storage({"records": [valid]})
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    durable = deepcopy(storage.data)
    durable["records"].append(malformed)

    async def lost_ack(_payload):
        storage.data = deepcopy(durable)
        raise OSError("lost acknowledgement")

    storage.async_save = lost_ack
    with pytest.raises(OSError, match="lost acknowledgement"):
        await memory.async_add(
            "device:kitchen",
            "Uncommitted",
            valid["expires_at"],
            owner_scope_id="user:alice",
        )
    assert memory.initialized
    assert set(memory._records) == {valid["memory_id"]}
    assert memory._records[valid["memory_id"]].content == valid["content"]


async def test_memory_owned_update_rejects_invalid_source_before_mutation():
    memory = await _memory()
    created = await memory.async_add("alice", "Keep this", "general", "explicit")
    before = await memory.async_backup_data()
    with pytest.raises(ValueError, match="source must be explicit or implicit"):
        await memory.async_update(
            "alice", created["memory"]["memory_id"], "Changed", source="unknown"
        )
    assert await memory.async_backup_data() == before


async def test_embedding_cache_recovers_after_repeated_save_failure(monkeypatch):
    memory = await _memory()
    store = SimpleNamespace(
        async_save=AsyncMock(side_effect=[OSError("full"), OSError("full"), None])
    )
    memory._embedding_cache_storage = store
    memory._embedding_cache_dirty = True
    assert not await memory._async_save_embedding_cache_locked()
    assert not await memory._async_save_embedding_cache_locked()
    assert memory._embedding_cache_write_failed and memory._embedding_cache_dirty
    assert await memory._async_save_embedding_cache_locked()
    assert (
        not memory._embedding_cache_write_failed and not memory._embedding_cache_dirty
    )
    assert store.async_save.await_count == 3


async def test_guest_restriction_without_end_preserves_existing_deadline(hass):
    manager = guest_manager(hass)
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    manager._schedule = guest_mode.GuestModeSchedule(
        "2026-09-08T10:00:00+00:00",
        "2026-09-08T18:00:00+00:00",
        "home_assistant",
        now.isoformat(),
    )
    await manager.async_restrict(active_from="2026-09-08T09:00:00+00:00", now=now)
    assert manager.schedule.active_from == "2026-09-08T09:00:00+00:00"
    assert manager.schedule.active_until == "2026-09-08T18:00:00+00:00"
    manager._store.async_save.assert_awaited_once()


async def test_guest_restriction_refuses_unavailable_persistence(hass):
    manager = guest_manager(hass)
    manager._persistence_unavailable = True
    with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
        await manager.async_restrict()
    manager._store.async_save.assert_not_awaited()
    assert manager.schedule is None


async def test_archive_loading_discards_orphan_turn_and_keeps_owned_turn():
    store = FakeArchiveStorage()
    archive = archives.ConversationArchive(store, "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    turn = await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="Keep",
        assistant_text="Reply",
        successful=True,
    )
    partition = next(iter(store.partitions))
    store.partitions[partition]["turns"].append(
        asdict(replace(turn, session_id="deleted-session", turn_id="orphan"))
    )
    restarted = archives.ConversationArchive(store, "agent")
    await restarted.async_initialize()
    assert restarted.stats()["turn_count"] == 1
    assert restarted._turns[session.session_id][0].turn_id == turn.turn_id
    assert "deleted-session" not in restarted._turns


async def test_archive_real_storage_refuses_access_during_restore(hass):
    storage = archives.HomeAssistantArchiveStorage(hass, "entry", "agent")
    archive = archives.ConversationArchive(storage, "agent")
    archive._initialized = True
    storage._metadata._recovery_gate.recovery_required = True
    with pytest.raises(HomeAssistantError):
        await archive.async_search("user:alice", "text")


async def test_archive_search_ranks_literal_phrase_above_token_only_match():
    archive = archives.ConversationArchive(FakeArchiveStorage(), "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    literal = await archive.async_record_turn(
        session.session_id,
        run_id="literal",
        user_text="kitchen light",
        assistant_text="Yes",
        successful=True,
    )
    token = await archive.async_record_turn(
        session.session_id,
        run_id="token",
        user_text="Kitchen temperature",
        assistant_text="The light is on",
        successful=True,
    )
    result = await history.archive_search_page(
        archive, "user:alice", "kitchen light", limit=10
    )
    assert [row["turn_id"] for row in result["results"]] == [
        literal.turn_id,
        token.turn_id,
    ]
    assert result["total"] == 2


async def test_usage_shutdown_defers_flush_if_retiring_request_requires_recovery(
    hass, monkeypatch
):
    store = RecoveryGuardedStore(hass, 1, "usage-transition").bind_agent(
        "entry", "agent"
    )
    manager = usage.UsageManager(store)
    started = asyncio.Event()

    async def request_owner():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            # The retiring operation discovers an indeterminate durable generation.
            store._recovery_gate.recovery_required = True

    owner = asyncio.create_task(request_owner())
    await started.wait()
    manager._active_runs["request"] = owner
    aggregates, details = AsyncMock(), AsyncMock()
    monkeypatch.setattr(manager, "_async_save_aggregates", aggregates)
    monkeypatch.setattr(manager, "_async_save_details", details)
    await manager.async_shutdown()
    assert owner.cancelled() and store.recovery_pending
    aggregates.assert_not_awaited()
    details.assert_not_awaited()
