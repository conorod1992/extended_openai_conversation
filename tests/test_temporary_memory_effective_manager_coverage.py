"""Coverage for effective Temporary Memory manager ownership contracts."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_DELETE_RECORDS,
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_ownership import (
    install_temporary_memory_ownership,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_performance import (
    install_temporary_memory_read_fast_path,
)
from homeassistant.util import dt as dt_util


# Exercise the same effective method composition used by integration startup.
install_temporary_memory_read_fast_path()
install_temporary_memory_ownership()


class Storage:
    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def future(hours: int = 1) -> str:
    return (dt_util.utcnow() + timedelta(hours=hours)).isoformat()


def stored_record(index: int, *, owner: str = "user:alice") -> dict:
    now = dt_util.utcnow() + timedelta(seconds=index)
    return {
        "memory_id": f"memory-{index:03d}",
        "scope_id": "device:kitchen",
        "owner_scope_id": owner,
        "content": f"fact-{index:03d}",
        "category": "general",
        "source": "automatic",
        "expires_at": future(24),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


async def test_management_lists_are_owner_scoped_and_require_owner() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()
    await memory.async_add(
        "device:kitchen",
        "Alice fact",
        future(),
        owner_scope_id="user:alice",
    )
    await memory.async_add(
        "device:kitchen",
        "Bob fact",
        future(),
        owner_scope_id="user:bob",
    )

    assert [
        item.content for item in await memory.async_list(owner_scope_id="user:alice")
    ] == ["Alice fact"]
    assert [
        item.content for item in await memory.async_list_all(owner_scope_id="user:bob")
    ] == ["Bob fact"]

    with pytest.raises(ValueError, match="resolved Personal or Shared owner"):
        await memory.async_list()
    with pytest.raises(ValueError, match="resolved Personal or Shared owner"):
        await memory.async_list_all(owner_scope_id="device:kitchen")


async def test_owned_management_mutations_cannot_cross_owner_boundary() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()
    alice = await memory.async_add(
        "device:kitchen",
        "Alice fact",
        future(),
        owner_scope_id="user:alice",
    )
    bob = await memory.async_add(
        "device:kitchen",
        "Bob fact",
        future(),
        owner_scope_id="user:bob",
    )

    alice_id = alice["memory"]["memory_id"]
    bob_id = bob["memory"]["memory_id"]
    with pytest.raises(ValueError, match="temporary memory not found"):
        await memory.async_update_owned("user:alice", bob_id, "stolen", None, None)
    assert await memory.async_delete_owned("user:alice", [bob_id]) == 0

    updated = await memory.async_update_owned(
        "user:alice", alice_id, "Alice updated", None, "general"
    )
    assert updated.content == "Alice updated"
    assert await memory.async_delete_owned("user:alice", [alice_id]) == 1

    with pytest.raises(ValueError, match="memory_ids must contain"):
        await memory.async_delete_owned("user:alice", [])
    with pytest.raises(ValueError, match="memory_ids must contain"):
        await memory.async_delete_owned(
            "user:alice", [f"missing-{index}" for index in range(MAX_DELETE_RECORDS + 1)]
        )


async def test_owner_counts_include_only_active_owned_records() -> None:
    active_alice = stored_record(1, owner="user:alice")
    active_bob = stored_record(2, owner="user:bob")
    expired_alice = stored_record(3, owner="user:alice")
    expired_alice["expires_at"] = (
        dt_util.utcnow() - timedelta(minutes=1)
    ).isoformat()

    memory = TemporaryMemory(
        Storage({"records": [active_alice, active_bob, expired_alice]})
    )
    await memory.async_initialize()

    assert memory.owner_counts() == {"user:alice": 1, "user:bob": 1}


async def test_backup_restore_enforces_global_record_ceiling_and_keeps_newest() -> None:
    raw = [stored_record(index) for index in range(MAX_ACTIVE_RECORDS + 4)]
    validated = TemporaryMemory.validate_backup_data({"records": raw})
    assert len(validated) == MAX_ACTIVE_RECORDS + 4

    storage = Storage()
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    await memory.async_replace_backup(validated)

    restored = await memory.async_list_owned("user:alice")
    assert len(restored) == MAX_ACTIVE_RECORDS
    ids = {record.memory_id for record in restored}
    assert f"memory-{MAX_ACTIVE_RECORDS + 3:03d}" in ids
    assert "memory-000" not in ids
    assert len(storage.data["records"]) == MAX_ACTIVE_RECORDS
