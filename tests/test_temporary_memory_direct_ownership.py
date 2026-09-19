"""Direct-owner structural, transactional, and request-isolation regressions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import inspect
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    temporary_memory as temporary,
)
from homeassistant.util import dt as dt_util


class Storage:
    def __init__(self, records=()):
        self.data = {"records": [asdict(record) for record in records]}
        self.fail_save = False
        self.cancel_save = False
        self.loads = 0
        self.saves = 0

    async def async_load(self):
        self.loads += 1
        return deepcopy(self.data)

    async def async_save(self, data):
        self.saves += 1
        if self.cancel_save:
            raise asyncio.CancelledError
        if self.fail_save:
            raise OSError("disk offline")
        self.data = deepcopy(data)


def record(memory_id, *, owner="user:alice", scope="device:kitchen", expired=False):
    now = dt_util.utcnow()
    return temporary.TemporaryMemoryRecord(
        memory_id,
        scope,
        f"Fact {memory_id}",
        "general",
        "automatic",
        (now + timedelta(hours=-1 if expired else 1)).isoformat(),
        now.isoformat(),
        now.isoformat(),
        owner,
    )


def test_temporary_memory_is_owned_without_installers_or_alias_repair():
    cls = temporary.TemporaryMemory
    names = (
        "__init__",
        "async_initialize",
        "async_active",
        "async_active_snapshot",
        "async_add",
        "async_update",
        "async_delete",
        "async_list",
        "async_list_all",
        "async_list_owned",
        "async_update_owned",
        "async_delete_owned",
        "owner_counts",
        "stats",
        "validate_backup_data",
        "async_replace_backup",
        "_async_save_locked",
    )
    originals = {name: getattr(cls, name) for name in names}
    for name, method in originals.items():
        assert method.__module__ == temporary.__name__
        assert method.__qualname__ == f"TemporaryMemory.{name}"
        assert not hasattr(method, "__wrapped__")
        assert Path(inspect.getsourcefile(method)).name == "temporary_memory.py"
    assert {name: getattr(cls, name) for name in names} == originals
    assert (
        management_ui.async_read_temporary_memory_snapshot
        is temporary.async_read_temporary_memory_snapshot
    )
    assert not hasattr(temporary, "_INSTALLED")
    for name in ("temporary_memory_ownership.py", "temporary_memory_performance.py"):
        assert not Path(temporary.__file__).with_name(name).exists()


async def test_failed_expiry_startup_resets_and_reloads_successfully():
    store = Storage([record("expired", expired=True), record("valid")])
    store.fail_save = True
    manager = temporary.TemporaryMemory(store)
    with pytest.raises(OSError, match="disk offline"):
        await manager.async_initialize()
    assert manager._records == {}
    assert not manager._initialized
    assert manager._committed_state is None
    assert manager.expired_pruned == 0
    store.fail_save = False
    await manager.async_initialize()
    assert [r.memory_id for r in await manager.async_list_owned("user:alice")] == [
        "valid"
    ]
    assert manager.expired_pruned == 1
    assert store.loads == 2


async def test_owner_normalization_failure_retries_without_reloading_or_leaking():
    store = Storage([record("valid"), record("unowned", owner=None)])
    store.fail_save = True
    manager = temporary.TemporaryMemory(store)
    with pytest.raises(OSError, match="disk offline"):
        await manager.async_initialize()
    assert manager._initialized
    assert manager.invalid_owners_pruned == 0
    assert [
        r.memory_id for r in await manager.async_active_snapshot("other", "user:alice")
    ] == ["valid"]
    assert await manager.async_active_snapshot("device:kitchen") == []
    store.fail_save = False
    await manager.async_initialize()
    assert set(manager._records) == {"valid"}
    assert manager.invalid_owners_pruned == 1
    assert store.loads == 1
    await manager.async_initialize()
    assert manager.invalid_owners_pruned == 1
    assert store.saves == 2


async def test_cancelled_store_save_rolls_back_and_next_mutation_recovers():
    store = Storage([record("valid")])
    manager = temporary.TemporaryMemory(store)
    await manager.async_initialize()
    store.cancel_save = True
    with pytest.raises(asyncio.CancelledError):
        await manager.async_delete_owned("user:alice", ["valid"])
    assert set(manager._records) == {"valid"}
    assert store.data["records"][0]["memory_id"] == "valid"
    store.cancel_save = False
    assert await manager.async_delete_owned("user:alice", ["valid"]) == 1
    assert store.data == {"records": []}


async def test_failed_deferred_prune_restores_baseline_but_expiry_stays_invisible():
    store = Storage([record("valid")])
    manager = temporary.TemporaryMemory(store)
    await manager.async_initialize()
    # Model time advancing, retaining a matching committed baseline for rollback.
    manager._records["valid"] = replace(
        manager._records["valid"],
        expires_at=(dt_util.utcnow() - timedelta(seconds=1)).isoformat(),
    )
    manager._remember_committed_state()
    store.fail_save = True
    assert await manager.async_active("different-continuity", "user:alice") == []
    task = manager._prune_save_task
    assert task is not None
    await task
    await asyncio.sleep(0)
    assert set(manager._records) == {"valid"}
    assert manager.expired_pruned == 0
    assert manager.owner_counts() == {}
    assert await manager.async_active_snapshot("any", "user:alice") == []
    assert manager._prune_save_task is None
    store.fail_save = False
    assert await manager.async_active("any", "user:alice") == []
    await manager._prune_save_task
    assert store.data == {"records": []}
    assert manager.expired_pruned == 1


async def test_parallel_request_contexts_never_cross_owners():
    store = Storage([record("alice"), record("bob", owner="user:bob")])
    manager = temporary.TemporaryMemory(store)
    await manager.async_initialize()
    ready = asyncio.Event()

    async def read(owner):
        token = temporary._ACTIVE_OWNER_SCOPE_ID.set(owner)
        try:
            await ready.wait()
            records = await manager.async_active("same-device")
            assert await manager.async_active("same-device", "device:kitchen") == []
            return [r.memory_id for r in records]
        finally:
            temporary._ACTIVE_OWNER_SCOPE_ID.reset(token)

    alice = asyncio.create_task(read("user:alice"))
    bob = asyncio.create_task(read("user:bob"))
    ready.set()
    assert await asyncio.gather(alice, bob) == [["alice"], ["bob"]]
    assert temporary._ACTIVE_OWNER_SCOPE_ID.get() is None
