"""Residual coverage for model search hardening guards and delegation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_search_hardening as hardening,
)


@pytest.mark.parametrize("arguments", [{}, {"query": None}, {"query": 123}])
def test_require_nonblank_query_rejects_missing_or_non_string_query(arguments) -> None:
    with pytest.raises(ValueError, match="query is required"):
        hardening._require_nonblank_query(arguments)


@pytest.mark.asyncio
async def test_knowledge_search_with_valid_query_delegates():
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )

    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"knowledge_enabled": True})
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    search = AsyncMock(return_value=[])
    agent._knowledge = SimpleNamespace(
        source_count=1,
        async_search=search,
        resolve_source_filter=lambda _ids: (None, []),
    )
    assert await agent._async_execute_knowledge_tool(
        "search", {"query": "temperature"}
    ) == {"results": []}
    search.assert_awaited_once_with("temperature", None, 5)


@pytest.mark.asyncio
async def test_archive_non_search_operation_does_not_require_query():
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )

    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"archive_enabled": True})
    expected = {"status": "ok"}
    get = AsyncMock(return_value=expected)
    agent._archive = SimpleNamespace(async_get=get)
    scope = owner._ACTIVE_SCOPE.set(SimpleNamespace(scope_id="user:alice"))
    active = owner._ACTIVE_ARCHIVE.set(("session", "id"))
    try:
        assert (
            await agent._async_execute_archive_tool("get", {"session_id": "item-1"})
            is expected
        )
    finally:
        owner._ACTIVE_SCOPE.reset(scope)
        owner._ACTIVE_ARCHIVE.reset(active)
    get.assert_awaited_once_with("user:alice", "item-1", 0, 6)
