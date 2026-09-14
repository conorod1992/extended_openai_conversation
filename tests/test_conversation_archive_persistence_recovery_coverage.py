"""Persistence recovery coverage for a loaded Conversation Archive manager."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


class FailingArchiveStorage:
    """In-memory archive storage with a one-shot partition failure."""

    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}
        self.fail_next_partition = False

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        if self.fail_next_partition:
            self.fail_next_partition = False
            raise OSError("partition write failed")
        self.partitions[partition] = deepcopy(data)


async def _archive_with_failed_turn():
    storage = FailingArchiveStorage()
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="test"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    storage.fail_next_partition = True
    with pytest.raises(OSError, match="partition write failed"):
        await archive.async_record_turn(
            session.session_id,
            run_id="run-1",
            user_text="first turn",
            assistant_text="first reply",
            successful=True,
        )

    assert archive._pending_partitions
    assert "pending_partitions" in storage.metadata
    return archive, storage, session


async def test_loaded_archive_converges_after_partition_failure_without_restart() -> None:
    """A later successful commit must absorb and clear an earlier pending journal."""
    archive, storage, session = await _archive_with_failed_turn()

    second = await archive.async_record_turn(
        session.session_id,
        run_id="run-2",
        user_text="second turn",
        assistant_text="second reply",
        successful=True,
    )
    assert second is not None
    assert archive._pending_partitions == set()
    assert "pending_partitions" not in storage.metadata

    # Prove convergence reached durable state rather than only repairing the
    # currently-loaded manager's in-memory view.
    restarted = ConversationArchive(storage, "agent-1")
    await restarted.async_initialize()
    result = await restarted.async_get("user:alice", session.session_id)
    assert [turn["user_text"] for turn in result["turns"]] == [
        "first turn",
        "second turn",
    ]
    assert result["session"]["turn_count"] == 2


async def test_later_session_publish_preserves_pending_transaction_for_restart() -> None:
    """Unrelated metadata writes cannot erase an unfinished partition journal."""
    archive, storage, failed_session = await _archive_with_failed_turn()

    later_session = await archive.async_begin_session(
        "kitchen",
        user_scope("bob", source="test"),
        "conversation-2",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert later_session is not None
    assert "pending_partitions" in storage.metadata
    persisted_ids = {item["session_id"] for item in storage.metadata["sessions"]}
    assert failed_session.session_id in persisted_ids
    assert later_session.session_id in persisted_ids

    # No successful commit happened on the loaded manager. Restart recovery
    # must still replay the original pending partition and retain the newer
    # session metadata written after the failure.
    restarted = ConversationArchive(storage, "agent-1")
    await restarted.async_initialize()

    failed = await restarted.async_get("user:alice", failed_session.session_id)
    assert [turn["user_text"] for turn in failed["turns"]] == ["first turn"]
    listed = await restarted.async_list_sessions("user:bob")
    assert [item["session_id"] for item in listed["sessions"]] == [
        later_session.session_id
    ]
    assert "pending_partitions" not in storage.metadata
