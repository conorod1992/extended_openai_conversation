"""Local embeddings survive replacement only for exact unchanged facts."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
    usage,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from tests.test_memory_v2 import FakeStorage
from tests.test_selective_restore_concurrency import RestoreHarness


async def _warm(records):
    durable, cache = FakeStorage(), FakeStorage()
    manager = PersistentMemory(durable, cache)
    await manager.async_initialize()
    await manager.async_replace_backup(records)
    calls = []

    async def embed(inputs):
        calls.append(list(inputs))
        return [[1.0, 0.0] for _ in inputs]

    manager.set_embedding_provider(embed, "model", space_id="provider-space")
    await manager.async_prepare_hybrid([records[0].user_id], "warm query")
    calls.clear()
    return manager, durable, cache, calls, embed


@pytest.mark.parametrize(
    "path", ["knowledge_commit", "later_rollback", "startup_target", "startup_rollback"]
)
@pytest.mark.parametrize("cold", [False, True])
async def test_unchanged_cache_survives_transaction_and_recovery(
    hass, monkeypatch, path, cold
):
    tx = RestoreHarness(monkeypatch)
    manager, durable, cache, calls, embed = await _warm(tx.state.memories)
    original_cache = deepcopy(cache.data)
    if cold:
        manager = PersistentMemory(durable, cache)
        await manager.async_initialize()
        assert manager._embedding_provider is None
    tx.managers = replace(tx.managers, memory=manager)
    if path == "knowledge_commit":
        await tx.restore()
    elif path == "later_rollback":
        tx.fail_knowledge_writes = 1
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await tx.restore()
    else:
        journal = restore_recovery._new_journal(
            tx.entry.entry_id, tx.subentry.subentry_id, tx.state, tx.state
        )
        if path == "startup_target":
            journal["phase"] = restore_recovery._PHASE_COMMITTED
        tx.journal.data = journal
        monkeypatch.setattr(usage, "async_verify_rejected_usage_stores", AsyncMock())
        assert await restore_recovery.async_recover_pending_restore(
            tx.hass, tx.entry, tx.subentry
        )
    assert tx.journal.data is None
    assert cache.data == original_cache
    manager.set_embedding_provider(embed, "model", space_id="provider-space")
    assert await manager.async_prepare_hybrid(
        [tx.state.memories[0].user_id], "next query"
    )
    assert calls == [["next query"]]
    assert durable.data["memories"]  # Replacement still writes canonical facts.


@pytest.mark.parametrize(
    "change", ["content", "metadata", "deleted", "fingerprint", "model", "space"]
)
async def test_changed_deleted_or_incompatible_entries_are_not_reused(hass, change):
    from tests.test_restore_recovery import _states

    records = _states()[0].memories
    manager, durable, cache, calls, embed = await _warm(records)
    memory_id = records[0].memory_id
    if change == "content":
        records = [replace(records[0], content="A changed fact")]
    elif change == "metadata":
        records = [replace(records[0], updated_at="2026-11-12T10:00:00+00:00")]
    elif change == "deleted":
        records = []
    elif change == "fingerprint":
        manager._embedding_cache[memory_id] = replace(
            manager._embedding_cache[memory_id], fingerprint="stale"
        )
    await manager.async_replace_backup(records)
    if change == "deleted":
        assert manager._embedding_cache == {}
        assert cache.data["embeddings"] == {}
        assert durable.data == {"memories": []}
        return
    if change in {"content", "metadata", "fingerprint"}:
        assert memory_id not in manager._embedding_cache
    if change in {"model", "space"}:
        manager.set_embedding_provider(
            embed,
            "other" if change == "model" else "model",
            space_id="other" if change == "space" else "provider-space",
        )
    await manager.async_prepare_hybrid([records[0].user_id], "next query")
    assert len(calls) == 2
    assert calls[-1] == ["next query"]


async def test_identical_replacement_repairs_damaged_store_and_preserves_scope(hass):
    from tests.test_restore_recovery import _states

    records = _states()[0].memories
    manager, durable, _cache, calls, _embed = await _warm(records)
    durable.data = {"memories": ["damaged"]}
    manager._repair_required = True
    await manager.async_replace_backup(records)
    assert not manager._repair_required
    assert PersistentMemory.validate_backup_data(durable.data) == records
    assert await manager.async_prepare_hybrid(["other-user"], "private query")
    assert calls == [["private query"]]
    assert (
        await manager.async_search(
            ["other-user"], "Aurora", hybrid=True, query_embedding=[1.0, 0.0]
        )
        == []
    )
