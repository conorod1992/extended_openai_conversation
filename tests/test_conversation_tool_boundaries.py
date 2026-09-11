"""Conversation-level orchestration tests for retained-data tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import conversation
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_TEMPORARY_MEMORY,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)


def _agent(options: dict | None = None) -> ExtendedOpenAIAgentEntity:
    """Build the narrow entity surface needed by the tool dispatchers."""
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    retained_data_options = {
        CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
    }
    retained_data_options.update(options or {})
    entity.subentry = SimpleNamespace(data=retained_data_options)
    entity._effective_guest_policy = lambda: SimpleNamespace(
        guest_active=False,
        shared_memory_read=True,
        shared_memory_write=True,
        temporary_memory=True,
    )
    return entity


def _llm_context(user_id: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(user_id=user_id))


def _memory_record() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-1",
        user_id="user-1",
        content="Oscar is the user's dog.",
        category="pets",
        source="explicit",
        created_at="2026-09-11T12:00:00+00:00",
        updated_at="2026-09-11T12:00:00+00:00",
        importance="high",
    )


def _temporary_record() -> TemporaryMemoryRecord:
    return TemporaryMemoryRecord(
        memory_id="temporary-1",
        scope_id="conversation:test",
        content="The oven is preheating.",
        category="activity",
        source="automatic",
        expires_at="2026-09-11T22:00:00+00:00",
        created_at="2026-09-11T20:00:00+00:00",
        updated_at="2026-09-11T20:05:00+00:00",
        owner_scope_id="user:user-1",
    )


async def test_persistent_memory_dispatcher_routes_read_and_write_operations() -> None:
    """The conversation boundary preserves scopes, options, and serialized results."""
    entity = _agent()
    record = _memory_record()
    entity._memory = SimpleNamespace(
        async_list=AsyncMock(return_value=[record]),
        async_update=AsyncMock(return_value=record),
        async_delete=AsyncMock(return_value=1),
    )
    entity._async_search_memories = AsyncMock(return_value=[record])
    llm_context = _llm_context()

    searched = await entity._async_execute_memory_tool(
        "search",
        {"query": "dog", "category": "pets", "limit": 3},
        llm_context,
    )
    listed = await entity._async_execute_memory_tool(
        "list",
        {"category": "pets", "limit": 4, "offset": 2},
        llm_context,
    )
    updated = await entity._async_execute_memory_tool(
        "update",
        {
            "memory_id": "memory-1",
            "content": "Oscar is a Cavachon.",
            "importance": "high",
            "clear_fields": ["subject"],
        },
        llm_context,
    )
    deleted = await entity._async_execute_memory_tool(
        "delete", {"memory_ids": ["memory-1"]}, llm_context
    )

    entity._async_search_memories.assert_awaited_once_with(
        ["user-1"], "dog", 3, "pets"
    )
    entity._memory.async_list.assert_awaited_once_with(
        ["user-1"], "pets", 4, 2
    )
    entity._memory.async_update.assert_awaited_once_with(
        "user-1",
        "memory-1",
        "Oscar is a Cavachon.",
        None,
        importance="high",
        clear_fields=["subject"],
    )
    entity._memory.async_delete.assert_awaited_once_with(
        "user-1", ["memory-1"]
    )
    assert searched["memories"][0]["memory_id"] == "memory-1"
    assert listed["memories"][0]["memory_id"] == "memory-1"
    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == "memory-1"
    assert deleted == {"status": "deleted", "deleted": 1}


async def test_temporary_memory_dispatcher_uses_request_scope_for_all_mutations() -> None:
    """Temporary-memory tools cannot silently escape the request-derived scope."""
    entity = _agent()
    record = _temporary_record()
    entity._temporary_memory = SimpleNamespace(
        async_add=AsyncMock(
            return_value={"status": "created", "memory": {"memory_id": "temporary-1"}}
        ),
        async_update=AsyncMock(return_value=record),
        async_delete=AsyncMock(return_value=1),
    )
    scope_token = conversation._ACTIVE_SCOPE.set(
        user_scope("user-1", source="test")
    )
    temporary_token = conversation._ACTIVE_TEMPORARY_SCOPE.set("conversation:test")
    try:
        created = await entity._async_execute_temporary_memory_tool(
            "add",
            {
                "content": "The oven is preheating.",
                "expires_at": "2026-09-11T22:00:00+00:00",
                "category": "activity",
            },
        )
        updated = await entity._async_execute_temporary_memory_tool(
            "update",
            {
                "memory_id": "temporary-1",
                "content": "The oven is ready.",
                "expires_at": "2026-09-11T22:30:00+00:00",
                "category": "activity",
            },
        )
        deleted = await entity._async_execute_temporary_memory_tool(
            "delete", {"memory_ids": ["temporary-1"]}
        )
    finally:
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temporary_token)
        conversation._ACTIVE_SCOPE.reset(scope_token)

    entity._temporary_memory.async_add.assert_awaited_once_with(
        "conversation:test",
        "The oven is preheating.",
        "2026-09-11T22:00:00+00:00",
        "activity",
    )
    entity._temporary_memory.async_update.assert_awaited_once_with(
        "conversation:test",
        "temporary-1",
        "The oven is ready.",
        "2026-09-11T22:30:00+00:00",
        "activity",
    )
    entity._temporary_memory.async_delete.assert_awaited_once_with(
        "conversation:test", ["temporary-1"]
    )
    assert created["status"] == "created"
    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == "temporary-1"
    assert deleted == {"status": "deleted", "deleted": 1}


async def test_archive_dispatcher_routes_session_operations_and_resume_boundary() -> None:
    """Archive tools use the active owner/session and publish a resumed session."""
    entity = _agent(
        {
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED: True,
            CONF_SHARED_ARCHIVE_ENABLED: True,
        }
    )
    resumed_session = SimpleNamespace(
        session_id="session-2", retention_state="retained"
    )
    entity._archive = SimpleNamespace(
        async_search=AsyncMock(return_value={"results": []}),
        async_get=AsyncMock(return_value={"session_id": "selected"}),
        async_make_private=AsyncMock(return_value={"deleted_turns": 2}),
        async_resume_saving=AsyncMock(return_value=resumed_session),
        async_delete_session=AsyncMock(return_value={"deleted_turns": 1}),
        async_delete_selected=AsyncMock(return_value={"deleted_sessions": 2}),
        async_delete_date_range=AsyncMock(return_value={"deleted_sessions": 3}),
    )
    scope = user_scope("alice", source="test")
    scope_token = conversation._ACTIVE_SCOPE.set(scope)
    archive_token = conversation._ACTIVE_ARCHIVE.set(("session-key", "session-1"))
    try:
        searched = await entity._async_execute_archive_tool(
            "search",
            {
                "query": "restaurant",
                "start_date": "2026-09-01",
                "end_date": "2026-09-11",
                "limit": 3,
            },
        )
        fetched = await entity._async_execute_archive_tool(
            "get", {"session_id": "selected", "start_turn": 2, "limit": 4}
        )
        private = await entity._async_execute_archive_tool("private", {})
        resumed = await entity._async_execute_archive_tool("resume", {})
        assert conversation._ACTIVE_ARCHIVE.get() == ("session-key", "session-2")
        current_deleted = await entity._async_execute_archive_tool(
            "delete_current", {}
        )
        selected_deleted = await entity._async_execute_archive_tool(
            "delete_selected",
            {"session_ids": ["session-2", "selected"], "confirm": True},
        )
        range_deleted = await entity._async_execute_archive_tool(
            "delete_range",
            {
                "start_date": "2026-09-01",
                "end_date": "2026-09-11",
                "confirm": True,
            },
        )
    finally:
        conversation._ACTIVE_ARCHIVE.reset(archive_token)
        conversation._ACTIVE_SCOPE.reset(scope_token)

    entity._archive.async_search.assert_awaited_once_with(
        "user:alice",
        "restaurant",
        start_date="2026-09-01",
        end_date="2026-09-11",
        limit=3,
    )
    entity._archive.async_get.assert_awaited_once_with(
        "user:alice", "selected", 2, 4
    )
    entity._archive.async_make_private.assert_awaited_once_with("session-1")
    entity._archive.async_resume_saving.assert_awaited_once_with(
        "session-key", "session-1", scope, shared_archive_enabled=True
    )
    entity._archive.async_delete_session.assert_awaited_once_with(
        "user:alice", "session-2"
    )
    entity._archive.async_delete_selected.assert_awaited_once_with(
        "user:alice", ["session-2", "selected"], confirm=True
    )
    entity._archive.async_delete_date_range.assert_awaited_once_with(
        "user:alice", "2026-09-01", "2026-09-11", confirm=True
    )
    assert searched == {"results": []}
    assert fetched == {"session_id": "selected"}
    assert private == {"deleted_turns": 2}
    assert resumed == {
        "private_mode_enabled": False,
        "session_id": "session-2",
        "future_turns_retained": True,
        "private_content_restored": False,
    }
    assert current_deleted == {"session_id": "session-2", "deleted_turns": 1}
    assert selected_deleted == {"deleted_sessions": 2}
    assert range_deleted == {"deleted_sessions": 3}


async def test_conversation_tool_dispatchers_reject_malformed_control_arguments() -> None:
    """Malformed routing/control arguments fail before reaching retained stores."""
    entity = _agent({CONF_ARCHIVE_MODEL_SEARCH_ENABLED: True})
    entity._memory = SimpleNamespace(async_list=AsyncMock())
    entity._temporary_memory = SimpleNamespace(async_delete=AsyncMock())
    entity._archive = SimpleNamespace(async_delete_date_range=AsyncMock())

    with pytest.raises(ValueError, match="query, category, or limit"):
        await entity._async_execute_memory_tool(
            "search", {"query": "dog", "limit": True}, _llm_context()
        )

    temporary_scope_token = conversation._ACTIVE_SCOPE.set(
        user_scope("user-1", source="test")
    )
    temporary_token = conversation._ACTIVE_TEMPORARY_SCOPE.set("conversation:test")
    try:
        with pytest.raises(ValueError, match="memory_ids must be a list of strings"):
            await entity._async_execute_temporary_memory_tool(
                "delete", {"memory_ids": "temporary-1"}
            )
    finally:
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temporary_token)
        conversation._ACTIVE_SCOPE.reset(temporary_scope_token)

    scope_token = conversation._ACTIVE_SCOPE.set(user_scope("alice", source="test"))
    archive_token = conversation._ACTIVE_ARCHIVE.set(("session-key", "session-1"))
    try:
        with pytest.raises(ValueError, match="start_date and end_date are required"):
            await entity._async_execute_archive_tool(
                "delete_range", {"start_date": "2026-09-01", "confirm": True}
            )
    finally:
        conversation._ACTIVE_ARCHIVE.reset(archive_token)
        conversation._ACTIVE_SCOPE.reset(scope_token)

    entity._memory.async_list.assert_not_awaited()
    entity._temporary_memory.async_delete.assert_not_awaited()
    entity._archive.async_delete_date_range.assert_not_awaited()
