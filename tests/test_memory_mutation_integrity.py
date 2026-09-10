"""Persistence-failure and concurrency contracts for persistent memory."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
)


class FailableStorage:
    """Detached storage that can fail exactly one durable save."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)
        self.fail_next_save = False

    async def async_load(self) -> dict[str, Any] | None:
        """Load a detached value."""
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        """Yield once so gathered mutations genuinely overlap at the manager lock."""
        await asyncio.sleep(0)
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("durable storage unavailable")
        self.data = deepcopy(data)


async def _memory(
    storage: FailableStorage | None = None,
    cache: FailableStorage | None = None,
) -> PersistentMemory:
    memory = PersistentMemory(storage or FailableStorage(), cache or FailableStorage())
    await memory.async_initialize()
    return memory


def _durable_manager_state(memory: PersistentMemory) -> dict[str, Any]:
    """Capture facts and indexes that must match the durable Memory store."""
    return {
        "memories": dict(memory._memories),
        "token_index": {
            key: frozenset(value) for key, value in memory._token_index.items()
        },
        "key_index": dict(memory._key_index),
    }


async def _warm_embedding_cache(memory: PersistentMemory) -> None:
    """Warm the regenerable cache so failed mutations exercise invalidation safely."""

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "integrity-test-model")
    assert await memory.async_prepare_hybrid(["alice"], "Oscar breed") == [1.0, 0.0]


@pytest.mark.parametrize(
    "operation",
    [
        "add",
        "keyed_upsert",
        "update",
        "delete",
        "clear",
        "reassign",
        "replace_backup",
    ],
)
async def test_durable_save_failure_rolls_back_live_manager(operation: str) -> None:
    """A failed durable write must not leave a newer in-process Memory view."""
    storage = FailableStorage()
    memory = await _memory(storage)
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
    )
    memory_id = created["memory"]["memory_id"]
    await _warm_embedding_cache(memory)

    before_live = _durable_manager_state(memory)
    before_disk = deepcopy(storage.data)
    replacement = [
        MemoryRecord(
            memory_id="replacement-memory",
            user_id="alice",
            content="Tea is kept in the pantry.",
            category="home",
            source="explicit",
            created_at="2026-09-10T09:00:00+00:00",
            updated_at="2026-09-10T09:00:00+00:00",
            key="home.tea.location",
            last_confirmed_at="2026-09-10T09:00:00+00:00",
        )
    ]

    storage.fail_next_save = True
    with pytest.raises(OSError, match="durable storage unavailable"):
        if operation == "add":
            await memory.async_add(
                "alice", "Breakfast is usually porridge.", "preferences", "explicit"
            )
        elif operation == "keyed_upsert":
            await memory.async_upsert(
                "alice",
                "Oscar is a Cavapoo.",
                "pets",
                "explicit",
                key="pet.oscar.breed",
            )
        elif operation == "update":
            await memory.async_update(
                "alice", memory_id, content="Oscar is a Cavapoo."
            )
        elif operation == "delete":
            await memory.async_delete("alice", [memory_id])
        elif operation == "clear":
            await memory.async_clear("alice")
        elif operation == "reassign":
            await memory.async_reassign("alice", "bob", [memory_id])
        else:
            await memory.async_replace_backup(replacement)

    assert _durable_manager_state(memory) == before_live
    assert storage.data == before_disk
    records = await memory.async_search("alice", "Oscar Cavachon breed")
    assert [record.memory_id for record in records] == [memory_id]

    # Embeddings are a separate regenerable cache, not part of the durable Memory
    # transaction. A failed mutation may conservatively invalidate them; prove that
    # doing so cannot strand the restored fact or leave a stale vector in use.
    assert await memory.async_prepare_hybrid(["alice"], "Oscar breed") == [1.0, 0.0]
    assert [record.memory_id for record in await memory.async_search(
        "alice",
        "Oscar breed",
        query_embedding=[1.0, 0.0],
        hybrid=True,
    )] == [memory_id]


async def test_concurrent_keyed_upserts_serialize_to_one_canonical_record() -> None:
    """Two simultaneous first writes to one key cannot create duplicate identities."""
    storage = FailableStorage()
    memory = await _memory(storage)

    first, second = await asyncio.gather(
        memory.async_upsert(
            "alice",
            "Oscar is a Cavachon.",
            "pets",
            "explicit",
            key="pet.oscar.breed",
        ),
        memory.async_upsert(
            "alice",
            "Oscar is a Cavapoo.",
            "pets",
            "explicit",
            key="pet.oscar.breed",
        ),
    )

    assert {first["status"], second["status"]} == {"created", "updated"}
    records = await memory.async_list("alice")
    assert len(records) == 1
    assert records[0].key == "pet.oscar.breed"
    assert records[0].content in {"Oscar is a Cavachon.", "Oscar is a Cavapoo."}
    assert memory._key_index == {("alice", "pet.oscar.breed"): records[0].memory_id}

    reloaded = await _memory(storage)
    assert await reloaded.async_backup_data() == await memory.async_backup_data()


async def test_concurrent_unkeyed_duplicate_adds_create_only_one_record() -> None:
    """Duplicate detection remains effective when identical adds start together."""
    storage = FailableStorage()
    memory = await _memory(storage)

    first, second = await asyncio.gather(
        memory.async_add("alice", "Tea is kept in the pantry.", "home", "explicit"),
        memory.async_add("alice", "Tea is kept in the pantry.", "home", "explicit"),
    )

    assert {first["status"], second["status"]} == {"created", "duplicate"}
    assert first["memory"]["memory_id"] == second["memory"]["memory_id"]
    assert len(await memory.async_list("alice")) == 1

    reloaded = await _memory(storage)
    assert await reloaded.async_backup_data() == await memory.async_backup_data()


async def test_delete_racing_update_cannot_resurrect_or_corrupt_indexes() -> None:
    """Whichever mutation obtains the lock first, deletion leaves one valid outcome."""
    storage = FailableStorage()
    memory = await _memory(storage)
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    memory_id = created["memory"]["memory_id"]

    update_result, delete_result = await asyncio.gather(
        memory.async_update("alice", memory_id, content="Oscar is a Cavapoo."),
        memory.async_delete("alice", [memory_id]),
        return_exceptions=True,
    )

    assert delete_result == 1
    assert isinstance(update_result, MemoryRecord | ValueError)
    assert await memory.async_list("alice") == []
    assert memory._key_index == {}
    assert all(memory_id not in ids for ids in memory._token_index.values())

    reloaded = await _memory(storage)
    assert await reloaded.async_list("alice") == []


async def test_reassign_racing_update_preserves_single_owner_and_indexes() -> None:
    """A scope move racing an old-owner edit cannot duplicate or misindex a memory."""
    storage = FailableStorage()
    memory = await _memory(storage)
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    memory_id = created["memory"]["memory_id"]

    update_result, reassign_result = await asyncio.gather(
        memory.async_update("alice", memory_id, content="Oscar is a Cavapoo."),
        memory.async_reassign("alice", "bob", [memory_id]),
        return_exceptions=True,
    )

    assert reassign_result == {"requested": 1, "reassigned": 1, "unchanged": 0}
    assert isinstance(update_result, MemoryRecord | ValueError)
    assert await memory.async_list("alice") == []
    bob_records = await memory.async_list("bob")
    assert len(bob_records) == 1
    assert bob_records[0].memory_id == memory_id
    assert bob_records[0].content in {"Oscar is a Cavachon.", "Oscar is a Cavapoo."}
    assert memory._key_index == {("bob", "pet.oscar.breed"): memory_id}

    reloaded = await _memory(storage)
    assert await reloaded.async_backup_data() == await memory.async_backup_data()
