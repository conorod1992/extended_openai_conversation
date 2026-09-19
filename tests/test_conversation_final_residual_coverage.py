"""Focused residual coverage for final conversation.py branches."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import conversation


def _conversation_method(name: str):
    """Return the stable conversation owner."""
    return getattr(conversation.ExtendedOpenAIAgentEntity, name)


@pytest.mark.asyncio
async def test_archive_turn_with_empty_chat_log_records_empty_assistant_text() -> None:
    record_turn = AsyncMock()
    entity = _agent(
        _archive=SimpleNamespace(async_record_turn=record_turn),
        _effective_guest_policy=MagicMock(
            return_value=SimpleNamespace(archive_retention=True)
        ),
    )

    await _conversation_method("_async_archive_turn")(
        entity,
        SimpleNamespace(session_id="session-1"),
        "run-1",
        SimpleNamespace(text="Hello"),
        SimpleNamespace(content=[]),
        successful=False,
    )

    record_turn.assert_awaited_once_with(
        "session-1",
        run_id="run-1",
        user_text="Hello",
        assistant_text="",
        successful=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_store", "no_readable_scopes"])
async def test_retrieve_memories_returns_empty_when_unavailable(mode: str) -> None:
    readable_scopes = MagicMock(return_value=[])
    rank_memories = AsyncMock()
    entity = _agent(
        _memory=None if mode == "missing_store" else SimpleNamespace(),
        _continuity=MagicMock(),
        _current_readable_memory_scope_ids=readable_scopes,
        _async_rank_memories=rank_memories,
        subentry=SimpleNamespace(data={"memory_mode": "automatic"}),
    )

    result = await _conversation_method("_async_retrieve_memories")(
        entity, SimpleNamespace(), "where are my keys"
    )

    assert result == []
    rank_memories.assert_not_awaited()
    if mode == "missing_store":
        readable_scopes.assert_not_called()
    else:
        readable_scopes.assert_called_once()


@pytest.mark.asyncio
async def test_retrieve_memories_failure_is_best_effort() -> None:
    rank_memories = AsyncMock(side_effect=RuntimeError("ranking failed"))
    entity = _agent(
        _memory=SimpleNamespace(),
        _continuity=None,
        _current_readable_memory_scope_ids=MagicMock(return_value=["user:alice"]),
        _async_rank_memories=rank_memories,
        subentry=SimpleNamespace(data={"memory_mode": "automatic"}),
    )

    result = await _conversation_method("_async_retrieve_memories")(
        entity, SimpleNamespace(), "remember this"
    )

    assert result == []
    rank_memories.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["guest_disabled", "missing_store", "missing_scope"],
)
async def test_retrieve_temporary_memories_returns_empty_when_unavailable(
    mode: str,
) -> None:
    active = AsyncMock(return_value=[])
    temporary_memory = SimpleNamespace(async_active=active)
    entity = _agent(
        _effective_guest_policy=MagicMock(
            return_value=SimpleNamespace(temporary_memory=mode != "guest_disabled")
        ),
        _temporary_memory=None if mode == "missing_store" else temporary_memory,
    )
    scope = None if mode == "missing_scope" else "conversation:test"
    token = conversation._ACTIVE_TEMPORARY_SCOPE.set(scope)
    try:
        result = await _conversation_method("_async_retrieve_temporary_memories")(
            entity
        )
    finally:
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(token)

    assert result == []
    active.assert_not_awaited()


def _agent(**attributes):
    agent = object.__new__(conversation.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"temporary_memory": "enabled"})
    agent.__dict__.update(attributes)
    return agent
