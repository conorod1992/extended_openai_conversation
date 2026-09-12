"""Focused persistence, ownership, expiry, and archive invariant tests."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_ownership import (
    install_temporary_memory_ownership,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_performance import (
    install_temporary_memory_read_fast_path,
)
from homeassistant.util import dt as dt_util

# Exercise the same effective method composition installed by integration startup.
install_temporary_memory_read_fast_path()
install_temporary_memory_ownership()


class MemoryStorage:
    """Detached persistence double for durable and temporary memory."""

    def __init__(self, data: Any = None) -> None:
        self.data = deepcopy(data)
        self.fail_save = False

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        if self.fail_save:
            raise OSError("memory write failed")
        self.data = deepcopy(data)


class ArchiveStorage:
    """Detached archive persistence double."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] | None = None
        self.partitions: dict[str, dict[str, Any]] = {}

    async def async_load_metadata(self) -> dict[str, Any] | None:
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data: dict[str, Any]) -> None:
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition: str) -> dict[str, Any] | None:
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(
        self, partition: str, data: dict[str, Any]
    ) -> None:
        self.partitions[partition] = deepcopy(data)


def _temporary_record(
    memory_id: str,
    *,
    expires_at: datetime,
    owner_scope_id: str | None = "user:alice",
) -> dict[str, Any]:
    created = datetime(2026, 9, 12, 10, 0, tzinfo=UTC).isoformat()
    return {
        "memory_id": memory_id,
        "scope_id": "device:kitchen",
        "owner_scope_id": owner_scope_id,
        "content": f"fact-{memory_id}",
        "category": "general",
        "source": "automatic",
        "expires_at": expires_at.isoformat(),
        "created_at": created,
        "updated_at": created,
    }


async def _archive() -> tuple[ConversationArchive, ArchiveStorage]:
    storage = ArchiveStorage()
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()
    return archive, storage


async def test_memory_corrupt_store_keeps_valid_record_and_self_heals() -> None:
    """One malformed persisted record does not discard unrelated valid memory."""
    storage = MemoryStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    created = await memory.async_add(
        "user-1", "Oscar is the user's dog.", "pets", "explicit"
    )
    valid_id = created["memory"]["memory_id"]

    storage.data["memories"].append({"memory_id": "broken"})

    restarted = PersistentMemory(storage)
    await restarted.async_initialize()

    assert [record.memory_id for record in await restarted.async_list("user-1")] == [
        valid_id
    ]
    assert [record["memory_id"] for record in storage.data["memories"]] == [valid_id]


async def test_temporary_memory_expiry_boundary_removes_only_expired_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact expiry instant is expired while a later record remains live."""
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    storage = MemoryStorage(
        {
            "records": [
                _temporary_record("boundary", expires_at=now),
                _temporary_record("live", expires_at=now + timedelta(microseconds=1)),
            ]
        }
    )
    memory = TemporaryMemory(storage)

    await memory.async_initialize()

    assert [
        record.memory_id for record in await memory.async_list_owned("user:alice")
    ] == ["live"]
    assert memory.stats()["expired_temporary_memories_pruned"] == 1
    assert [record["memory_id"] for record in storage.data["records"]] == ["live"]

    monkeypatch.setattr(
        dt_util, "utcnow", lambda: now + timedelta(microseconds=1)
    )
    assert await memory.async_list_owned("user:alice") == []
    assert storage.data == {"records": []}


async def test_temporary_memory_capacity_rejects_record_beyond_live_limit() -> None:
    """The runtime ceiling rejects record 101 without evicting a live record."""
    now = dt_util.utcnow()
    records = [
        _temporary_record(
            f"memory-{index:03d}", expires_at=now + timedelta(days=1)
        )
        for index in range(MAX_ACTIVE_RECORDS)
    ]
    storage = MemoryStorage({"records": records})
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    before = deepcopy(storage.data)

    with pytest.raises(ValueError, match="temporary memory limit reached"):
        await memory.async_add(
            "device:kitchen",
            "one record too many",
            (now + timedelta(hours=1)).isoformat(),
            owner_scope_id="user:alice",
        )

    assert storage.data == before
    assert len(await memory.async_list_owned("user:alice")) == MAX_ACTIVE_RECORDS


async def test_temporary_memory_owner_normalization_save_failure_retries_cleanly() -> (
    None
):
    """Failed owner migration is rolled back and can be retried without data loss."""
    now = dt_util.utcnow()
    raw = _temporary_record(
        "legacy", expires_at=now + timedelta(hours=1), owner_scope_id=None
    )
    raw["scope_id"] = "user:alice"
    storage = MemoryStorage({"records": [raw]})
    storage.fail_save = True
    memory = TemporaryMemory(storage)

    with pytest.raises(OSError, match="memory write failed"):
        await memory.async_initialize()

    assert storage.data["records"][0]["owner_scope_id"] is None

    storage.fail_save = False
    await memory.async_initialize()
    owned = await memory.async_list_owned("user:alice")

    assert [record.memory_id for record in owned] == ["legacy"]
    assert owned[0].owner_scope_id == "user:alice"
    assert storage.data["records"][0]["owner_scope_id"] == "user:alice"


async def test_archive_list_order_and_offset_pagination_are_stable() -> None:
    """Archive metadata is newest-first and pages without overlap."""
    archive, _ = await _archive()
    scope = user_scope("alice", source="test")
    sessions = []
    for index in range(3):
        session = await archive.async_begin_session(
            f"key-{index}",
            scope,
            f"conversation-{index}",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )
        await archive.async_record_turn(
            session.session_id,
            run_id=f"run-{index}",
            user_text=f"question {index}",
            assistant_text=f"answer {index}",
            successful=True,
        )
        sessions.append(session.session_id)

    first_page = await archive.async_list_sessions("user:alice", limit=2, offset=0)
    second_page = await archive.async_list_sessions("user:alice", limit=2, offset=2)

    assert [item["session_id"] for item in first_page["sessions"]] == [
        sessions[2],
        sessions[1],
    ]
    assert first_page["has_more"] is True
    assert [item["session_id"] for item in second_page["sessions"]] == [sessions[0]]
    assert second_page["has_more"] is False


async def test_archive_get_turn_pagination_is_exact() -> None:
    """Reading a transcript page preserves turn order and has_more semantics."""
    archive, _ = await _archive()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for index in range(3):
        await archive.async_record_turn(
            session.session_id,
            run_id=f"run-{index}",
            user_text=f"question {index}",
            assistant_text=f"answer {index}",
            successful=True,
        )

    page = await archive.async_get("user:alice", session.session_id, 1, 1)

    assert [turn["user_text"] for turn in page["turns"]] == ["question 1"]
    assert page["start_turn"] == 1
    assert page["limit"] == 1
    assert page["has_more"] is True


async def test_archive_delete_missing_record_errors_per_current_contract() -> None:
    """Deleting the same retained session twice reports not-found on retry."""
    archive, _ = await _archive()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="question",
        assistant_text="answer",
        successful=True,
    )

    assert await archive.async_delete_session("user:alice", session.session_id) == {
        "deleted_sessions": 1,
        "deleted_turns": 1,
    }
    with pytest.raises(ValueError, match="conversation session not found"):
        await archive.async_delete_session("user:alice", session.session_id)


async def test_cross_owner_archive_delete_is_denied_before_mutation() -> None:
    """A foreign scope cannot delete or alter another owner's retained session."""
    archive, _ = await _archive()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="private question",
        assistant_text="private answer",
        successful=True,
    )

    with pytest.raises(ValueError, match="conversation session not found"):
        await archive.async_delete_session("user:bob", session.session_id)

    retained = await archive.async_get("user:alice", session.session_id)
    assert [turn["user_text"] for turn in retained["turns"]] == ["private question"]


async def test_archive_malformed_session_is_ignored_without_losing_valid_session() -> (
    None
):
    """Startup skips one malformed session while preserving unrelated archive data."""
    archive, storage = await _archive()
    session = await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    storage.metadata["sessions"].append({"session_id": "broken"})

    restarted = ConversationArchive(storage, "agent-1")
    await restarted.async_initialize()
    listed = await restarted.async_list_sessions("user:alice")

    assert [item["session_id"] for item in listed["sessions"]] == [session.session_id]
    assert restarted.stats()["session_count"] == 1
