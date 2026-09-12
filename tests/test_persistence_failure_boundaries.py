"""Fault-injection tests for durable persistence failure boundaries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening,
    persistence_hardening,
)


@dataclass(frozen=True)
class _ArchiveSession:
    session_id: str
    retention_state: str = "retained"


@dataclass(frozen=True)
class _ArchiveTurn:
    session_id: str
    timestamp: str


class _ArchiveStorage:
    def __init__(
        self,
        *,
        fail_metadata: bool = False,
        fail_partition: bool = False,
    ) -> None:
        self.fail_metadata = fail_metadata
        self.fail_partition = fail_partition
        self.metadata_calls: list[dict[str, Any]] = []
        self.partition_calls: list[tuple[str, dict[str, Any]]] = []

    async def async_save_metadata(self, payload: dict[str, Any]) -> None:
        self.metadata_calls.append(payload)
        if self.fail_metadata:
            raise RuntimeError("metadata write failed")

    async def async_save_partition(
        self, partition: str, payload: dict[str, Any]
    ) -> None:
        self.partition_calls.append((partition, payload))
        if self.fail_partition:
            raise RuntimeError("partition write failed")


class _Archive:
    def __init__(self, storage: _ArchiveStorage) -> None:
        self._storage = storage
        self._sessions = {"old": _ArchiveSession("old")}
        self._turns = defaultdict(
            list,
            {"old": [_ArchiveTurn("old", "2026-08-01T00:00:00+00:00")]},
        )
        self._active = {"scope": "old"}
        self._partitions = {"2026-08"}
        self._pending_partitions: set[str] = set()


@pytest.mark.asyncio
async def test_manager_save_failure_restores_committed_state_and_allows_retry() -> None:
    """A failed save rolls RAM back and does not poison the next save."""

    class Manager:
        def __init__(self) -> None:
            self.value = "initial"
            self.fail_save = False

        async def async_initialize(self) -> None:
            self.value = "committed"

        async def _async_save_locked(self) -> None:
            if self.fail_save:
                raise RuntimeError("save failed")

    def snapshot(manager: Manager) -> str:
        return manager.value

    def restore(manager: Manager, value: str) -> None:
        manager.value = value

    def reset(manager: Manager) -> None:
        manager.value = "reset"

    persistence_hardening._install_manager_guard(Manager, snapshot, restore, reset)
    manager = Manager()
    await manager.async_initialize()

    manager.value = "uncommitted"
    manager.fail_save = True
    with pytest.raises(RuntimeError, match="save failed"):
        await manager._async_save_locked()

    assert manager.value == "committed"

    manager.fail_save = False
    manager.value = "next-commit"
    await manager._async_save_locked()
    assert manager.value == "next-commit"

    manager.value = "later-uncommitted"
    manager.fail_save = True
    with pytest.raises(RuntimeError, match="save failed"):
        await manager._async_save_locked()

    assert manager.value == "next-commit"


@pytest.mark.asyncio
async def test_archive_journal_failure_does_not_publish_candidate_state() -> None:
    """Failure at the durable intent write leaves the previous live state intact."""
    storage = _ArchiveStorage(fail_metadata=True)
    archive = _Archive(storage)
    old_sessions = archive._sessions
    old_turns = archive._turns
    old_active = archive._active
    old_partitions = archive._partitions

    sessions = {"new": _ArchiveSession("new")}
    turns = {"new": [_ArchiveTurn("new", "2026-09-01T00:00:00+00:00")]}

    with pytest.raises(RuntimeError, match="metadata write failed"):
        await durable_state_hardening._async_commit_archive_state(
            archive,
            sessions=sessions,
            turns=turns,
            active={"scope": "new"},
            partitions={"2026-09"},
            changed_partitions={"2026-09"},
        )

    assert archive._sessions is old_sessions
    assert archive._turns is old_turns
    assert archive._active is old_active
    assert archive._partitions is old_partitions
    assert archive._pending_partitions == set()
    assert storage.partition_calls == []


@pytest.mark.asyncio
async def test_archive_partition_failure_keeps_published_state_marked_pending() -> None:
    """After the journal commit, a partition failure leaves restart-recoverable intent."""
    storage = _ArchiveStorage(fail_partition=True)
    archive = _Archive(storage)
    sessions = {"new": _ArchiveSession("new")}
    turns = {"new": [_ArchiveTurn("new", "2026-09-01T00:00:00+00:00")]}
    active = {"scope": "new"}

    with pytest.raises(RuntimeError, match="partition write failed"):
        await durable_state_hardening._async_commit_archive_state(
            archive,
            sessions=sessions,
            turns=turns,
            active=active,
            partitions={"2026-09"},
            changed_partitions={"2026-09"},
        )

    assert archive._sessions is sessions
    assert dict(archive._turns) == turns
    assert archive._active is active
    assert archive._partitions == {"2026-09"}
    assert archive._pending_partitions == {"2026-08", "2026-09"}
    assert len(storage.metadata_calls) == 1
    assert storage.metadata_calls[0]["partitions"] == ["2026-09"]
    assert set(storage.metadata_calls[0]["pending_partitions"]) == {
        "2026-08",
        "2026-09",
    }
