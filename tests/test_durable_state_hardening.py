"""Regression tests for journal-first Archive and Usage state mutations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses import (
    lifecycle_optimizations as lifecycle,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_RETENTION_DAYS,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageRequest,
    UsageRun,
)
from homeassistant.util import dt as dt_util


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


class FakeUsageStorage:
    """In-memory Usage Store whose writes can be failed on demand."""

    def __init__(self) -> None:
        self.data = None
        self.fail = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail:
            raise OSError("usage detail write failed")
        self.data = deepcopy(data)


async def _archive() -> tuple[ConversationArchive, FakeArchiveStorage]:
    hardening._install_archive_transactions()
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


def _usage_request(timestamp: str) -> UsageRequest:
    return UsageRequest(
        request_id="request",
        run_id="run",
        timestamp=timestamp,
        agent_subentry_id="agent",
        provider="openai",
        model="model",
        api_mode="responses",
        successful=True,
        duration_ms=1,
    )


def _usage_run(timestamp: str) -> UsageRun:
    return UsageRun(
        run_id="run",
        started_at=timestamp,
        completed_at=timestamp,
        duration_ms=1,
        agent_subentry_id="agent",
        home_assistant_conversation_id=None,
        source_device_id=None,
    )


async def _usage_manager() -> tuple[UsageManager, FakeUsageStorage]:
    hardening._install_usage_transactions()
    detail = FakeUsageStorage()
    manager = UsageManager(
        FakeUsageStorage(),
        FakeUsageStorage(),
        detail,
        agent_subentry_id="agent",
        request_retention_days=1,
        run_retention_days=1,
    )
    await manager.async_initialize()
    return manager, detail


async def test_usage_prune_failure_preserves_live_detail_lists() -> None:
    manager, detail = await _usage_manager()
    old = "2000-01-01T00:00:00+00:00"
    manager.requests = [_usage_request(old)]
    manager.runs = [_usage_run(old)]
    detail.fail = True

    with pytest.raises(OSError, match="usage detail write failed"):
        await manager.async_prune_details()

    assert [item.request_id for item in manager.requests] == ["request"]
    assert [item.run_id for item in manager.runs] == ["run"]


async def test_usage_clear_failure_preserves_live_detail_lists() -> None:
    manager, detail = await _usage_manager()
    now = dt_util.utcnow().isoformat()
    manager.requests = [_usage_request(now)]
    manager.runs = [_usage_run(now)]
    detail.fail = True

    with pytest.raises(OSError, match="usage detail write failed"):
        await manager.async_clear_details(confirm=True)

    assert [item.request_id for item in manager.requests] == ["request"]
    assert [item.run_id for item in manager.runs] == ["run"]


async def test_background_usage_prune_is_transactional_and_remains_off_path() -> None:
    manager, detail = await _usage_manager()
    old = "2000-01-01T00:00:00+00:00"
    manager.requests = [_usage_request(old)]
    manager.runs = [_usage_run(old)]
    if hasattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE):
        delattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE)
    if hasattr(manager, hardening._USAGE_PRUNE_ATTEMPT_DATE):
        delattr(manager, hardening._USAGE_PRUNE_ATTEMPT_DATE)
    detail.fail = True

    await lifecycle._async_prune_usage_if_due(manager)
    task = getattr(manager, hardening._USAGE_PRUNE_TASK)
    assert task is not None
    await task

    assert len(manager.requests) == 1
    assert len(manager.runs) == 1
    assert getattr(manager, hardening._USAGE_PRUNE_ATTEMPT_DATE) == (
        dt_util.utcnow().date().isoformat()
    )

    # Allow the next simulated daily attempt and prove the successful Store write is
    # what makes the candidate retention state visible.
    delattr(manager, hardening._USAGE_PRUNE_ATTEMPT_DATE)
    detail.fail = False
    await lifecycle._async_prune_usage_if_due(manager)
    await getattr(manager, hardening._USAGE_PRUNE_TASK)
    assert manager.requests == []
    assert manager.runs == []


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

    await hardening.async_prune_archive_retention(agent)

    assert archive.days == [17]
