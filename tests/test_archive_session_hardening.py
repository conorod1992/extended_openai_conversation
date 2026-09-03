"""Tests for transactional and fail-open archive session persistence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.archive_session_hardening import (
    _async_begin_session_transactional,
    _async_resume_saving_transactional,
    attach_archive_runtime_status,
    install_archive_session_hardening,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


class FailingArchiveStorage:
    """Minimal archive storage with controllable metadata failures."""

    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}
        self.fail_metadata = False

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        if self.fail_metadata:
            raise OSError("metadata write failed")
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)


async def _archive(storage: FailingArchiveStorage) -> ConversationArchive:
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()
    return archive


async def test_begin_failure_does_not_publish_unpersisted_session() -> None:
    storage = FailingArchiveStorage()
    archive = await _archive(storage)
    storage.fail_metadata = True

    with pytest.raises(OSError, match="metadata write failed"):
        await _async_begin_session_transactional(
            archive,
            "conversation:test",
            user_scope("alice", source="test"),
            "conversation-id",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )

    assert archive.active_session("conversation:test") is None
    assert archive.stats()["session_count"] == 0

    storage.fail_metadata = False
    session = await _async_begin_session_transactional(
        archive,
        "conversation:test",
        user_scope("alice", source="test"),
        "conversation-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert session is not None
    assert archive.active_session("conversation:test") == session
    assert archive.stats()["session_count"] == 1


async def test_resume_failure_keeps_previous_active_session_and_retries() -> None:
    storage = FailingArchiveStorage()
    archive = await _archive(storage)
    previous = await _async_begin_session_transactional(
        archive,
        "conversation:test",
        user_scope("alice", source="test"),
        "conversation-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert previous is not None
    storage.fail_metadata = True

    with pytest.raises(OSError, match="metadata write failed"):
        await _async_resume_saving_transactional(
            archive,
            "conversation:test",
            previous.session_id,
            user_scope("alice", source="test"),
        )

    assert archive.active_session("conversation:test") == previous
    assert archive.stats()["session_count"] == 1

    storage.fail_metadata = False
    resumed = await _async_resume_saving_transactional(
        archive,
        "conversation:test",
        previous.session_id,
        user_scope("alice", source="test"),
    )

    assert resumed.session_id != previous.session_id
    assert archive.active_session("conversation:test") == resumed
    assert archive.stats()["session_count"] == 2


async def test_session_metadata_write_preserves_pending_partition_journal() -> None:
    storage = FailingArchiveStorage()
    archive = await _archive(storage)
    archive._pending_partitions.add("2026-09")

    session = await _async_begin_session_transactional(
        archive,
        "conversation:test",
        user_scope("alice", source="test"),
        "conversation-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert session is not None
    assert storage.metadata["pending_partitions"] == {"2026-09": {"turns": []}}


class FakeAgent:
    def __init__(self) -> None:
        self.subentry = SimpleNamespace(data={CONF_ARCHIVE_ENABLED: True})
        self.statuses: list[tuple[str, bool, Exception | None, bool]] = []

    def _set_subsystem_status(
        self,
        subsystem: str,
        configured: bool,
        error: Exception | None = None,
        *,
        healthy: bool = False,
    ) -> None:
        self.statuses.append((subsystem, configured, error, healthy))


async def test_begin_failure_is_fail_open_and_later_success_restores_health() -> None:
    install_archive_session_hardening()
    storage = FailingArchiveStorage()
    archive = await _archive(storage)
    agent = FakeAgent()
    attach_archive_runtime_status(agent, archive)
    storage.fail_metadata = True

    failed = await archive.async_begin_session(
        "conversation:test",
        user_scope("alice", source="test"),
        "conversation-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert failed is None
    assert archive.active_session("conversation:test") is None
    assert archive.stats()["session_count"] == 0
    assert agent.statuses[-1][0:2] == ("archive", True)
    assert isinstance(agent.statuses[-1][2], OSError)
    assert agent.statuses[-1][3] is False

    storage.fail_metadata = False
    recovered = await archive.async_begin_session(
        "conversation:test",
        user_scope("alice", source="test"),
        "conversation-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert recovered is not None
    assert archive.active_session("conversation:test") == recovered
    assert agent.statuses[-1] == ("archive", True, None, True)
