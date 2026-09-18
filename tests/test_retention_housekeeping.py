"""Regression tests for Archive partition and Usage retention housekeeping."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening as hardening,
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


class FakeArchiveStorage:
    """In-memory partition storage that records physical partition removals."""

    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}
        self.removed_partitions: list[str] = []

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)

    async def async_remove_partition(self, partition):
        self.removed_partitions.append(partition)
        self.partitions.pop(partition, None)


class FakeUsageStorage:
    """In-memory Usage storage with a switchable transient write failure."""

    def __init__(self) -> None:
        self.data = None
        self.fail = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail:
            raise OSError("usage detail write failed")
        self.data = deepcopy(data)


async def test_destructive_archive_operation_drops_empty_month_partition() -> None:
    hardening._install_archive_transactions()
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
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
        user_text="secret",
        assistant_text="reply",
        successful=True,
    )
    partition = next(iter(archive._partitions))
    assert partition in storage.partitions

    result = await archive.async_make_private(session.session_id)

    assert result["deleted_turns"] == 1
    assert archive._partitions == set()
    assert storage.metadata["partitions"] == []
    assert partition in storage.removed_partitions
    assert partition not in storage.partitions


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
    old = "2000-01-01T00:00:00+00:00"
    manager.requests = [_usage_request(old)]
    manager.runs = [_usage_run(old)]
    return manager, detail


async def _run_due_prune(manager: UsageManager) -> None:
    await manager._async_prune_usage_if_due()
    task = manager._prune_task
    if isinstance(task, asyncio.Task):
        await task
        await asyncio.sleep(0)


async def test_usage_retention_retries_once_same_day_after_transient_failure() -> None:
    manager, detail = await _usage_manager()
    detail.fail = True

    await _run_due_prune(manager)

    today = dt_util.utcnow().date().isoformat()
    assert manager.requests
    assert manager.runs
    assert manager._prune_attempt_date == today
    assert manager._prune_attempt_count == 1
    assert manager._last_prune_date != today
    assert manager._next_prune_retry > 0

    # The cooldown prevents request traffic from immediately hammering storage.
    await manager._async_prune_usage_if_due()
    assert manager._prune_attempt_count == 1

    # Simulate the five-minute cooldown elapsing without crossing the UTC day.
    manager._next_prune_retry = 0.0
    detail.fail = False
    await _run_due_prune(manager)

    assert manager.requests == []
    assert manager.runs == []
    assert manager._prune_attempt_count == 2
    assert manager._last_prune_date == today
    assert manager._next_prune_retry == 0.0


async def test_usage_retention_same_day_retry_is_bounded_after_repeated_failures() -> None:
    manager, detail = await _usage_manager()
    detail.fail = True

    await _run_due_prune(manager)
    manager._next_prune_retry = 0.0
    await _run_due_prune(manager)

    assert manager._prune_attempt_count == 2
    assert manager.requests
    assert manager.runs

    # Even if the cooldown is considered elapsed again, two failed attempts are the
    # daily cap. A new UTC day resets the counter naturally through ATTEMPT_DATE.
    manager._next_prune_retry = 0.0
    await manager._async_prune_usage_if_due()
    await asyncio.sleep(0)

    assert manager._prune_attempt_count == 2
    assert manager._prune_task is None
