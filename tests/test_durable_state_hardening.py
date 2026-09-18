"""Regression tests for journal-first Archive state mutations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.conversation import ExtendedOpenAIAgentEntity

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_RETENTION_DAYS,
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
