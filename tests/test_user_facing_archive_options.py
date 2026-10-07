"""User-facing Conversation Archive option contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.extended_openai_conversation_responses import conversation_archive
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    user_scope,
)


class _Storage:
    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)

    async def async_remove_partition(self, partition):
        self.partitions.pop(partition, None)


async def _archive() -> ConversationArchive:
    archive = ConversationArchive(_Storage(), "agent-1")
    await archive.async_initialize()
    return archive


@pytest.mark.asyncio
async def test_archive_save_and_shared_retention_options_are_enforced() -> None:
    archive = await _archive()
    personal = user_scope("alice", source="test")
    shared = shared_scope(source="test")

    assert await archive.async_begin_session(
        "off", personal, "conversation-off",
        archive_enabled=False, shared_archive_enabled=True, inactivity_minutes=30,
    ) is None

    shared_off = await archive.async_begin_session(
        "shared-off", shared, "conversation-shared-off",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    assert shared_off is not None
    assert shared_off.retention_state == "unretained"
    assert await archive.async_record_turn(
        shared_off.session_id, run_id="run-1", user_text="private household text",
        assistant_text="not retained", successful=True,
    ) is None

    shared_on = await archive.async_begin_session(
        "shared-on", shared, "conversation-shared-on",
        archive_enabled=True, shared_archive_enabled=True, inactivity_minutes=30,
    )
    assert shared_on is not None
    assert shared_on.retention_state == "retained"
    assert await archive.async_record_turn(
        shared_on.session_id, run_id="run-2", user_text="shared retained text",
        assistant_text="retained", successful=True,
    ) is not None


@pytest.mark.asyncio
async def test_archive_session_timeout_option_changes_session_boundary(monkeypatch) -> None:
    archive = await _archive()
    scope = user_scope("alice", source="test")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(conversation_archive.dt_util, "utcnow", lambda: now)

    first = await archive.async_begin_session(
        "browser", scope, "conversation-1",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    assert first is not None

    now = now + timedelta(minutes=29)
    inside = await archive.async_begin_session(
        "browser", scope, "conversation-1",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    assert inside.session_id == first.session_id

    now = now + timedelta(minutes=31)
    outside = await archive.async_begin_session(
        "browser", scope, "conversation-1",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    assert outside.session_id != first.session_id


@pytest.mark.asyncio
async def test_archive_retention_prune_removes_only_expired_sessions(monkeypatch) -> None:
    archive = await _archive()
    scope = user_scope("alice", source="test")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(conversation_archive.dt_util, "utcnow", lambda: now - timedelta(days=31))
    old = await archive.async_begin_session(
        "old", scope, "old-conversation",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    await archive.async_record_turn(
        old.session_id, run_id="old", user_text="old text",
        assistant_text="old reply", successful=True,
    )

    monkeypatch.setattr(conversation_archive.dt_util, "utcnow", lambda: now - timedelta(days=29))
    fresh = await archive.async_begin_session(
        "fresh", scope, "fresh-conversation",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    await archive.async_record_turn(
        fresh.session_id, run_id="fresh", user_text="fresh text",
        assistant_text="fresh reply", successful=True,
    )

    monkeypatch.setattr(conversation_archive.dt_util, "utcnow", lambda: now)
    result = await archive.async_prune(30)
    assert result == {"deleted_sessions": 1, "deleted_turns": 1}
    listed = await archive.async_list_sessions("user:alice")
    assert [item["session_id"] for item in listed["sessions"]] == [fresh.session_id]
