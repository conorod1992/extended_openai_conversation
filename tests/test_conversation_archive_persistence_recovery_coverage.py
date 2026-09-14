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


async def test_loaded_archive_converges_after_partition_failure_without_restart() -> None:
    """A later successful commit must absorb and clear an earlier pending journal."""
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
