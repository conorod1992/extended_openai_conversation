"""Residual coverage for model search hardening guards and delegation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_search_hardening as hardening,
)


@pytest.mark.parametrize("arguments", [{}, {"query": None}, {"query": 123}])
def test_require_nonblank_query_rejects_missing_or_non_string_query(arguments) -> None:
    with pytest.raises(ValueError, match="query is required"):
        hardening._require_nonblank_query(arguments)


@pytest.mark.asyncio
async def test_knowledge_search_with_valid_query_delegates() -> None:
    expected = {"status": "ok"}
    original_knowledge = AsyncMock(return_value=expected)

    class FakeAgent:
        _async_rank_memories = AsyncMock()
        _async_execute_memory_tool = AsyncMock()
        _async_execute_knowledge_tool = original_knowledge
        _async_execute_archive_tool = AsyncMock()

    hardening._install_on_agent_class(FakeAgent)
    agent = SimpleNamespace()
    arguments = {"query": "temperature"}

    result = await FakeAgent._async_execute_knowledge_tool(agent, "search", arguments)

    assert result is expected
    original_knowledge.assert_awaited_once_with(agent, "search", arguments)


@pytest.mark.asyncio
async def test_archive_non_search_operation_does_not_require_query(monkeypatch) -> None:
    expected = {"status": "ok"}
    original_archive = AsyncMock(return_value=expected)

    class FakeAgent:
        _async_rank_memories = AsyncMock()
        _async_execute_memory_tool = AsyncMock()
        _async_execute_knowledge_tool = AsyncMock()
        _async_execute_archive_tool = original_archive

    require_query = Mock(side_effect=AssertionError("query guard should be bypassed"))
    monkeypatch.setattr(hardening, "_require_nonblank_query", require_query)
    hardening._install_on_agent_class(FakeAgent)
    agent = SimpleNamespace()
    arguments = {"memory_id": "item-1"}

    result = await FakeAgent._async_execute_archive_tool(agent, "get", arguments)

    assert result is expected
    original_archive.assert_awaited_once_with(agent, "get", arguments)
    require_query.assert_not_called()
