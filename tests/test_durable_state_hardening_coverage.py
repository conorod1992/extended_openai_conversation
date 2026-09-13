"""Focused residual coverage for durable state hardening."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive,
    durable_state_hardening as hardening,
)


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

    async def async_save_partition(
        self, partition: str, data: dict[str, Any]
    ) -> None:
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


def _session(
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


def _archive(
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
        turn.timestamp[:7]
        for session_turns in turns.values()
        for turn in session_turns
    }
    archive._pending_partitions = set()
    return archive


@pytest.fixture
def archive_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fresh wrappers while restoring the class exactly after each test."""
    archive_type = conversation_archive.ConversationArchive
    method_names = (
        "async_record_turn",
        "async_make_private",
        "async_delete_session",
        "async_clear_scope",
        "async_delete_selected",
        "async_prune",
        "async_replace_backup",
    )
    for name in method_names:
        monkeypatch.setattr(archive_type, name, getattr(archive_type, name))

    marker_owner = archive_type.async_record_turn
    monkeypatch.setattr(
        marker_owner, "_extended_openai_journal_first", False, raising=False
    )
    hardening._install_archive_transactions()


@pytest.mark.asyncio
async def test_remove_archive_partition_uses_legacy_store_fallback() -> None:
    store = FallbackPartitionStore()
    storage = LegacyArchiveStorage(store)

    await hardening._async_remove_archive_partition(storage, "2026-01")

    assert storage.requested == ["2026-01"]
    assert store.removed is True


@pytest.mark.asyncio
async def test_remove_archive_partition_tolerates_missing_legacy_hooks() -> None:
    await hardening._async_remove_archive_partition(NoRemovalArchiveStorage(), "2026-01")

    storage = LegacyArchiveStorage(SimpleNamespace())
    await hardening._async_remove_archive_partition(storage, "2026-02")
    assert storage.requested == ["2026-02"]


@pytest.mark.asyncio
async def test_archive_commit_does_not_publish_ram_before_journal_write() -> None:
    storage = RecordingArchiveStorage()
    original = _session("original")
    archive = _archive(storage, [original], {"original": [_turn("original")]})
    storage.fail_metadata = True
    candidate = _session("candidate", scope_id="scope-b")

    with pytest.raises(RuntimeError, match="metadata write failed"):
        await hardening._async_commit_archive_state(
            archive,
            sessions={"candidate": candidate},
            turns={"candidate": [_turn("candidate", month="2026-02")]},
            active={"candidate-key": "candidate"},
            partitions={"2026-02"},
            changed_partitions={"2026-02"},
        )

    assert set(archive._sessions) == {"original"}
    assert set(archive._turns) == {"original"}
    assert archive._partitions == {"2026-01"}
    assert storage.events[0][0] == "metadata"
    assert not any(event[0] == "partition" for event in storage.events)


@pytest.mark.asyncio
async def test_archive_commit_removal_failure_is_housekeeping_only(caplog) -> None:
    storage = RecordingArchiveStorage()
    old = _session("old")
    archive = _archive(storage, [old], {"old": [_turn("old", month="2026-01")]})
    storage.fail_remove = True

    await hardening._async_commit_archive_state(
        archive,
        sessions={},
        turns={},
        active={},
        partitions=set(),
        changed_partitions={"2026-01"},
    )

    assert archive._sessions == {}
    assert archive._partitions == set()
    assert archive._pending_partitions == set()
    assert storage.partitions["2026-01"] == {"turns": []}
    assert "Unable to remove obsolete conversation archive partition 2026-01" in caplog.text
    assert "pending_partitions" in storage.metadata[0]
    assert "pending_partitions" not in storage.metadata[-1]


@pytest.mark.asyncio
async def test_clear_scope_requires_confirmation_and_handles_empty_scope(
    archive_transactions: None,
) -> None:
    storage = RecordingArchiveStorage()
    archive = _archive(storage, [_session("a")], {"a": [_turn("a")]})

    with pytest.raises(ValueError, match="Explicit confirmation"):
        await archive.async_clear_scope("scope-a", confirm=False)

    result = await archive.async_clear_scope("missing", confirm=True)

    assert result == {"deleted_sessions": 0, "deleted_turns": 0}
    assert storage.events == []


@pytest.mark.asyncio
async def test_clear_scope_removes_all_owned_sessions_and_active_references(
    archive_transactions: None,
) -> None:
    storage = RecordingArchiveStorage()
    a = _session("a", scope_id="scope-a")
    b = _session("b", scope_id="scope-a")
    keep = _session("keep", scope_id="scope-b")
    archive = _archive(
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
async def test_delete_selected_validates_request_then_commits_all_targets(
    archive_transactions: None,
) -> None:
    storage = RecordingArchiveStorage()
    a = _session("a")
    b = _session("b")
    archive = _archive(
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
async def test_delete_selected_checks_scope_ownership_before_mutating(
    archive_transactions: None,
) -> None:
    storage = RecordingArchiveStorage()
    archive = _archive(
        storage,
        [_session("foreign", scope_id="scope-b")],
        {"foreign": [_turn("foreign")]},
    )

    with pytest.raises(ValueError, match="conversation session not found"):
        await archive.async_delete_selected("scope-a", ["foreign"], confirm=True)

    assert set(archive._sessions) == {"foreign"}
    assert storage.events == []


@pytest.mark.asyncio
async def test_replace_backup_rebuilds_partitions_and_clears_active_state(
    archive_transactions: None,
) -> None:
    storage = RecordingArchiveStorage()
    old = _session("old")
    archive = _archive(storage, [old], {"old": [_turn("old", month="2026-01")]})
    replacement = _session("replacement", scope_id="scope-b")
    replacement_turn = _turn("replacement", month="2026-04")

    await archive.async_replace_backup([replacement], [replacement_turn])

    assert set(archive._sessions) == {"replacement"}
    assert archive._turns == {"replacement": [replacement_turn]}
    assert archive._active == {}
    assert archive._partitions == {"2026-04"}
    assert storage.partitions["2026-01"] == {"turns": []}
    assert storage.partitions["2026-04"] == {"turns": [asdict(replacement_turn)]}


@dataclass(frozen=True)
class UsageRequest:
    timestamp: str
    name: str


@dataclass(frozen=True)
class UsageRun:
    started_at: str
    name: str


class DetailStorage:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def async_save(self, value: dict[str, Any]) -> None:
        self.saved.append(value)


@pytest.mark.asyncio
async def test_persist_usage_details_supports_disabled_and_enabled_storage() -> None:
    request = UsageRequest("2026-09-13T10:00:00+00:00", "request")
    run = UsageRun("2026-09-13T10:00:00+00:00", "run")
    manager = SimpleNamespace(_detail_storage=None)

    await hardening._async_persist_usage_details(manager, [request], [run])

    storage = DetailStorage()
    manager._detail_storage = storage
    await hardening._async_persist_usage_details(manager, [request], [run])

    assert storage.saved == [
        {
            "requests": [{"timestamp": request.timestamp, "name": "request"}],
            "runs": [{"started_at": run.started_at, "name": "run"}],
        }
    ]


@pytest.mark.asyncio
async def test_archive_retention_no_archive_and_failure_are_safe(caplog) -> None:
    await hardening.async_prune_archive_retention(SimpleNamespace(_archive=None))

    class FailingArchive:
        async def async_prune(self, retention_days: int) -> None:
            assert retention_days == 23
            raise RuntimeError("prune failed")

    agent = SimpleNamespace(
        _archive=FailingArchive(),
        subentry=SimpleNamespace(data={hardening.CONF_ARCHIVE_RETENTION_DAYS: 23}),
    )
    await hardening.async_prune_archive_retention(agent)

    assert "Background conversation archive retention maintenance failed" in caplog.text
