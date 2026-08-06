"""Tests for scoped archive search, privacy, deletion, and session stability."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    unretained_scope,
    user_scope,
)


class FakeArchiveStorage:
    def __init__(self):
        self.metadata = None
        self.partitions = {}

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)


async def _archive():
    archive = ConversationArchive(FakeArchiveStorage(), "agent-1")
    await archive.async_initialize()
    return archive


async def test_archive_is_unretained_when_disabled_or_scope_is_unresolved() -> None:
    archive = await _archive()
    disabled = await archive.async_begin_session(
        "one", user_scope("alice", source="authenticated_user"), "conversation-1",
        archive_enabled=False, shared_archive_enabled=False, inactivity_minutes=30,
    )
    unresolved = await archive.async_begin_session(
        "two", unretained_scope(device_id="satellite"), "conversation-2",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30,
    )
    assert disabled.retention_state == "unretained"
    assert unresolved.retention_state == "unretained"
    assert await archive.async_record_turn(disabled.session_id, run_id="run", user_text="secret", assistant_text="reply", successful=True) is None
    assert archive.stats()["turn_count"] == 0


async def test_scope_is_stable_and_different_sessions_are_not_joined() -> None:
    archive = await _archive()
    alice = user_scope("alice", source="device_mapping", device_id="kitchen")
    first = await archive.async_begin_session("key", alice, "conversation-1", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    same = await archive.async_begin_session("key", alice, "conversation-1", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    changed = await archive.async_begin_session("key", user_scope("bob", source="device_mapping", device_id="kitchen"), "conversation-1", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    assert same.session_id == first.session_id
    assert changed.session_id != first.session_id
    assert changed.scope_id == "user:bob"


async def test_private_mode_deletes_only_active_session_and_resume_is_new_boundary() -> None:
    archive = await _archive()
    scope = user_scope("alice", source="authenticated_user")
    active = await archive.async_begin_session("browser", scope, "one", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    other = await archive.async_begin_session("satellite", scope, "two", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    await archive.async_record_turn(active.session_id, run_id="run-1", user_text="medical appointment", assistant_text="call on Tuesday", successful=True)
    await archive.async_record_turn(other.session_id, run_id="run-2", user_text="weather", assistant_text="sunny", successful=True)
    result = await archive.async_make_private(active.session_id)
    assert result["deleted_turns"] == 1
    assert archive.stats()["turn_count"] == 1
    assert await archive.async_record_turn(active.session_id, run_id="run-3", user_text="private", assistant_text="private", successful=True) is None
    resumed = await archive.async_resume_saving("browser", active.session_id, scope)
    assert resumed.session_id != active.session_id
    assert resumed.retention_state == "retained"
    assert archive.stats()["turn_count"] == 1


async def test_search_and_get_cannot_cross_scope_or_return_whole_archive() -> None:
    archive = await _archive()
    alice = await archive.async_begin_session("a", user_scope("alice", source="authenticated_user"), "one", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    await archive.async_record_turn(alice.session_id, run_id="run-1", user_text="Which restaurant was it?", assistant_text="The locally owned Cedar House.", successful=True)
    found = await archive.async_search("user:alice", "restaurant cedar", limit=1)
    assert len(found["results"]) == 1
    assert len(found["results"][0]["excerpt"]) <= 502
    assert (await archive.async_search("user:bob", "restaurant cedar"))["results"] == []
    with pytest.raises(ValueError, match="not found"):
        await archive.async_get("user:bob", alice.session_id)


async def test_shared_archive_requires_explicit_agent_permission() -> None:
    archive = await _archive()
    blocked = await archive.async_begin_session("shared-1", shared_scope(source="shared_voice_policy"), "one", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    allowed = await archive.async_begin_session("shared-2", shared_scope(source="shared_voice_policy"), "two", archive_enabled=True, shared_archive_enabled=True, inactivity_minutes=30)
    assert blocked.retention_state == "unretained"
    assert allowed.retention_state == "retained"
