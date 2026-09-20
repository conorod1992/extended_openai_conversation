"""Regression tests for journal-first Archive state mutations."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_RETENTION_DAYS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


class FakeArchiveStorage:
    """Partitioned in-memory Archive storage with deterministic write failures."""

    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}
        self.metadata_save_count = 0
        self.partition_save_count = 0
        self.fail_metadata_on: int | None = None
        self.fail_partition_on: int | None = None

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata_save_count += 1
        if self.metadata_save_count == self.fail_metadata_on:
            raise OSError("metadata write failed")
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partition_save_count += 1
        if self.partition_save_count == self.fail_partition_on:
            raise OSError("partition write failed")
        self.partitions[partition] = deepcopy(data)


async def _archive() -> tuple[ConversationArchive, FakeArchiveStorage]:
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    return archive, storage


async def _session(archive: ConversationArchive):
    return await archive.async_begin_session(
        "key",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )


async def test_archive_first_journal_failure_does_not_publish_new_turn() -> None:
    archive, storage = await _archive()
    session = await _session(archive)
    storage.fail_metadata_on = storage.metadata_save_count + 1

    with pytest.raises(OSError, match="metadata write failed"):
        await archive.async_record_turn(
            session.session_id,
            run_id="run",
            user_text="hello",
            assistant_text="hi",
            successful=True,
        )

    assert archive.stats()["turn_count"] == 0
    assert archive.active_session("key").turn_count == 0
    assert (await archive.async_get("user:alice", session.session_id))["turns"] == []
    assert "pending_partitions" not in storage.metadata


async def test_archive_first_journal_failure_does_not_publish_deletion() -> None:
    archive, storage = await _archive()
    session = await _session(archive)
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="keep me",
        assistant_text="kept",
        successful=True,
    )
    storage.fail_metadata_on = storage.metadata_save_count + 1

    with pytest.raises(OSError, match="metadata write failed"):
        await archive.async_delete_session("user:alice", session.session_id)

    assert archive.active_session("key").session_id == session.session_id
    result = await archive.async_get("user:alice", session.session_id)
    assert [turn["user_text"] for turn in result["turns"]] == ["keep me"]


async def test_archive_post_journal_failure_keeps_recoverable_target_live() -> None:
    archive, storage = await _archive()
    session = await _session(archive)
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="secret",
        assistant_text="reply",
        successful=True,
    )
    storage.fail_partition_on = storage.partition_save_count + 1

    with pytest.raises(OSError, match="partition write failed"):
        await archive.async_make_private(session.session_id)

    # The journal is already durable, so RAM must match the state restart recovery
    # will finish rather than rolling back to content that is now scheduled to erase.
    assert archive.stats()["turn_count"] == 0
    assert archive.active_session("key").retention_state == "private"
    assert "pending_partitions" in storage.metadata

    storage.fail_partition_on = None
    restarted = ConversationArchive(storage, "agent")
    await restarted.async_initialize()
    assert restarted.stats()["turn_count"] == 0
    assert restarted.active_session("key").retention_state == "private"
    assert "pending_partitions" not in storage.metadata


async def test_archive_prune_clears_active_mapping_and_noop_is_write_free() -> None:
    archive, storage = await _archive()
    session = await _session(archive)
    archive._sessions[session.session_id] = replace(
        archive._sessions[session.session_id],
        last_message_at="2000-01-01T00:00:00+00:00",
    )

    result = await archive.async_prune(1)

    assert result == {"deleted_sessions": 1, "deleted_turns": 0}
    assert archive.active_session("key") is None
    assert archive.stats()["session_count"] == 0

    metadata_writes = storage.metadata_save_count
    partition_writes = storage.partition_save_count
    assert await archive.async_prune(1) == {
        "deleted_sessions": 0,
        "deleted_turns": 0,
    }
    assert storage.metadata_save_count == metadata_writes
    assert storage.partition_save_count == partition_writes


async def test_archive_retention_maintenance_reads_current_live_setting() -> None:
    class Archive:
        def __init__(self) -> None:
            self.days: list[int] = []

        async def async_prune(self, days: int) -> None:
            self.days.append(days)

    archive = Archive()
    agent = SimpleNamespace(
        _archive=archive,
        subentry=SimpleNamespace(data={CONF_ARCHIVE_RETENTION_DAYS: 17}),
    )

    await ExtendedOpenAIAgentEntity._async_prune_archive_retention(agent)

    assert archive.days == [17]


class RecordingArchiveStorage:
    """Small persistence fake that records the journal/partition write order."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.metadata: list[dict[str, Any]] = []
        self.partitions: dict[str, dict[str, Any]] = {}
        self.removed: list[str] = []
        self.fail_metadata = False
        self.fail_remove = False

    async def async_save_metadata(self, data: dict[str, Any]) -> None:
        self.events.append(("metadata", data))
        if self.fail_metadata:
            raise RuntimeError("metadata write failed")
        self.metadata.append(data)

    async def async_save_partition(self, partition: str, data: dict[str, Any]) -> None:
        self.events.append(("partition", partition, data))
        self.partitions[partition] = data

    async def async_remove_partition(self, partition: str) -> None:
        self.events.append(("remove", partition))
        if self.fail_remove:
            raise RuntimeError("remove failed")
        self.removed.append(partition)


class FallbackPartitionStore:
    def __init__(self) -> None:
        self.removed = False

    async def async_remove(self) -> None:
        self.removed = True


class LegacyArchiveStorage:
    """Archive storage exposing only the legacy private partition-store hook."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.requested: list[str] = []

    def _partition_store(self, partition: str) -> Any:
        self.requested.append(partition)
        return self.store


class NoRemovalArchiveStorage:
    pass


def _stored_session(
    session_id: str,
    *,
    scope_id: str = "scope-a",
    last_message_at: str = "2026-01-10T12:00:00+00:00",
) -> conversation_archive.ArchiveSession:
    return conversation_archive.ArchiveSession(
        session_id=session_id,
        home_assistant_conversation_id=f"conversation-{session_id}",
        agent_subentry_id="subentry",
        scope_id=scope_id,
        scope_type="user",
        scope_source="test",
        source_device_id=None,
        started_at=last_message_at,
        last_message_at=last_message_at,
        title=session_id,
        turn_count=1,
        retention_state="retained",
    )


def _turn(
    session_id: str,
    *,
    month: str = "2026-01",
) -> conversation_archive.ArchiveTurn:
    return conversation_archive.ArchiveTurn(
        turn_id=f"turn-{session_id}-{month}",
        session_id=session_id,
        run_id=None,
        timestamp=f"{month}-10T12:00:00+00:00",
        user_text="hello",
        assistant_text="hi",
        successful=True,
    )


def _seeded_archive(
    storage: Any,
    sessions: list[conversation_archive.ArchiveSession],
    turns: dict[str, list[conversation_archive.ArchiveTurn]],
) -> conversation_archive.ConversationArchive:
    archive = conversation_archive.ConversationArchive(storage, "subentry")
    archive._initialized = True
    archive._sessions = {session.session_id: session for session in sessions}
    archive._turns = defaultdict(list, turns)
    archive._active = {
        f"key-{session.session_id}": session.session_id for session in sessions
    }
    archive._partitions = {
        turn.timestamp[:7] for session_turns in turns.values() for turn in session_turns
    }
    archive._pending_partitions = set()
    return archive


@pytest.mark.asyncio
async def test_remove_archive_partition_uses_legacy_store_fallback() -> None:
    store = FallbackPartitionStore()
    storage = LegacyArchiveStorage(store)

    archive = _seeded_archive(storage, [], {})
    await archive._async_remove_partition_locked("2026-01")

    assert storage.requested == ["2026-01"]
    assert store.removed is True


@pytest.mark.asyncio
async def test_remove_archive_partition_tolerates_missing_legacy_hooks() -> None:
    archive = _seeded_archive(NoRemovalArchiveStorage(), [], {})
    await archive._async_remove_partition_locked("2026-01")

    storage = LegacyArchiveStorage(SimpleNamespace())
    archive = _seeded_archive(storage, [], {})
    await archive._async_remove_partition_locked("2026-02")
    assert storage.requested == ["2026-02"]


@pytest.mark.asyncio
async def test_archive_commit_removal_failure_is_housekeeping_only(caplog) -> None:
    storage = RecordingArchiveStorage()
    old = _stored_session("old")
    archive = _seeded_archive(storage, [old], {"old": [_turn("old", month="2026-01")]})
    storage.fail_remove = True

    await archive._async_commit_state_locked(
        sessions={},
        turns={},
        active={},
        changed_partitions={"2026-01"},
    )

    assert archive._sessions == {}
    assert archive._partitions == set()
    assert archive._pending_partitions == set()
    assert storage.partitions["2026-01"] == {"turns": []}
    assert (
        "Unable to remove obsolete conversation archive partition 2026-01"
        in caplog.text
    )
    assert "pending_partitions" in storage.metadata[0]
    assert "pending_partitions" not in storage.metadata[-1]


@pytest.mark.asyncio
async def test_clear_scope_requires_confirmation_and_handles_empty_scope() -> None:
    storage = RecordingArchiveStorage()
    archive = _seeded_archive(storage, [_stored_session("a")], {"a": [_turn("a")]})

    with pytest.raises(ValueError, match="Explicit confirmation"):
        await archive.async_clear_scope("scope-a", confirm=False)

    result = await archive.async_clear_scope("missing", confirm=True)

    assert result == {"deleted_sessions": 0, "deleted_turns": 0}
    assert storage.events == []


@pytest.mark.asyncio
async def test_clear_scope_removes_all_owned_sessions_and_active_references() -> None:
    storage = RecordingArchiveStorage()
    a = _stored_session("a", scope_id="scope-a")
    b = _stored_session("b", scope_id="scope-a")
    keep = _stored_session("keep", scope_id="scope-b")
    archive = _seeded_archive(
        storage,
        [a, b, keep],
        {
            "a": [_turn("a", month="2026-01")],
            "b": [_turn("b", month="2026-02")],
            "keep": [_turn("keep", month="2026-03")],
        },
    )

    result = await archive.async_clear_scope("scope-a", confirm=True)

    assert result == {"deleted_sessions": 2, "deleted_turns": 2}
    assert set(archive._sessions) == {"keep"}
    assert set(archive._turns) == {"keep"}
    assert set(archive._active.values()) == {"keep"}
    assert archive._partitions == {"2026-03"}
    assert storage.partitions["2026-01"] == {"turns": []}
    assert storage.partitions["2026-02"] == {"turns": []}


@pytest.mark.asyncio
async def test_delete_selected_validates_request_then_commits_all_targets() -> None:
    storage = RecordingArchiveStorage()
    a = _stored_session("a")
    b = _stored_session("b")
    archive = _seeded_archive(
        storage,
        [a, b],
        {"a": [_turn("a")], "b": [_turn("b")]},
    )

    with pytest.raises(ValueError, match="Explicit confirmation"):
        await archive.async_delete_selected("scope-a", ["a"], confirm=False)
    with pytest.raises(ValueError, match="session_ids must contain"):
        await archive.async_delete_selected("scope-a", [], confirm=True)
    with pytest.raises(ValueError, match="session_ids must contain"):
        await archive.async_delete_selected(
            "scope-a",
            [str(index) for index in range(conversation_archive.MAX_SEARCH_LIMIT + 1)],
            confirm=True,
        )

    result = await archive.async_delete_selected("scope-a", ["a", "b"], confirm=True)

    assert result == {"deleted_sessions": 2, "deleted_turns": 2}
    assert archive._sessions == {}
    assert dict(archive._turns) == {}
    assert archive._active == {}
    assert archive._partitions == set()


@pytest.mark.asyncio
async def test_delete_selected_checks_scope_ownership_before_mutating() -> None:
    storage = RecordingArchiveStorage()
    archive = _seeded_archive(
        storage,
        [_stored_session("foreign", scope_id="scope-b")],
        {"foreign": [_turn("foreign")]},
    )

    with pytest.raises(ValueError, match="conversation session not found"):
        await archive.async_delete_selected("scope-a", ["foreign"], confirm=True)

    assert set(archive._sessions) == {"foreign"}
    assert storage.events == []


@pytest.mark.asyncio
async def test_replace_backup_rebuilds_partitions_and_clears_active_state() -> None:
    storage = RecordingArchiveStorage()
    old = _stored_session("old")
    archive = _seeded_archive(storage, [old], {"old": [_turn("old", month="2026-01")]})
    replacement = _stored_session("replacement", scope_id="scope-b")
    replacement_turn = _turn("replacement", month="2026-04")

    await archive.async_replace_backup([replacement], [replacement_turn])

    assert set(archive._sessions) == {"replacement"}
    assert archive._turns == {"replacement": [replacement_turn]}
    assert archive._active == {}
    assert archive._partitions == {"2026-04"}
    assert storage.partitions["2026-01"] == {"turns": []}
    assert storage.partitions["2026-04"] == {"turns": [asdict(replacement_turn)]}


@pytest.mark.asyncio
async def test_archive_retention_no_archive_and_failure_are_safe(caplog) -> None:
    await ExtendedOpenAIAgentEntity._async_prune_archive_retention(
        SimpleNamespace(_archive=None)
    )

    class FailingArchive:
        async def async_prune(self, retention_days: int) -> None:
            assert retention_days == 23
            raise RuntimeError("prune failed")

    agent = SimpleNamespace(
        _archive=FailingArchive(),
        subentry=SimpleNamespace(data={"archive_retention_days": 23}),
    )
    await ExtendedOpenAIAgentEntity._async_prune_archive_retention(agent)

    assert "Background conversation archive retention maintenance failed" in caplog.text
