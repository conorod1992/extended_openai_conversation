"""Focused coverage for conversation runtime decision paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_RETRIEVAL_MODE,
    MEMORY_RETRIEVAL_HYBRID,
)

Agent = conversation_module.ExtendedOpenAIAgentEntity


class _MemoryRetrievalHarness(Agent):
    """Bind the production retrieval method to a minimal realistic agent surface."""

    def __init__(
        self,
        *,
        memory: object,
        continuity: object,
        scopes: list[str],
        retrieve_limit: int,
        rank: AsyncMock,
    ) -> None:
        self._memory = memory
        self._continuity = continuity
        self._scopes = scopes
        self._rank = rank
        self.subentry = SimpleNamespace(
            data={
                CONF_MEMORY_AUTO_RETRIEVE_LIMIT: retrieve_limit,
                "memory_mode": "automatic",
            }
        )

    def _current_readable_memory_scope_ids(self, _context: object) -> list[str]:
        return self._scopes

    async def _async_rank_memories(
        self, scopes: list[str], query: str, limit: int
    ) -> list[object]:
        return await self._rank(scopes, query, limit)


@pytest.mark.asyncio
async def test_memory_retrieval_selects_and_pins_continuity_bundle() -> None:
    """The first turn selects once and persists exact memory references."""
    selected = [
        SimpleNamespace(user_id="user-1", memory_id="memory-1"),
        SimpleNamespace(user_id="household", memory_id="memory-2"),
    ]
    references = [("user-1", "memory-1"), ("household", "memory-2")]
    resolved = [object(), object()]
    continuity = SimpleNamespace(
        async_get_memory_bundle=AsyncMock(return_value=None),
        async_set_memory_bundle=AsyncMock(return_value=references),
    )
    memory = SimpleNamespace(async_get_many=AsyncMock(return_value=resolved))
    rank = AsyncMock(return_value=selected)
    agent = _MemoryRetrievalHarness(
        memory=memory,
        continuity=continuity,
        scopes=["user-1", "household"],
        retrieve_limit=2,
        rank=rank,
    )
    token = conversation_module._ACTIVE_MEMORY_SESSION.set(("session-1", 30))
    try:
        result = await agent._async_retrieve_memories(object(), "where are keys")
    finally:
        conversation_module._ACTIVE_MEMORY_SESSION.reset(token)

    assert result == resolved
    continuity.async_get_memory_bundle.assert_awaited_once_with("session-1", 30)
    rank.assert_awaited_once_with(["user-1", "household"], "where are keys", 2)
    continuity.async_set_memory_bundle.assert_awaited_once_with(
        "session-1", references, 30
    )
    memory.async_get_many.assert_awaited_once_with(references, ["user-1", "household"])


@pytest.mark.asyncio
async def test_memory_retrieval_reuses_existing_bundle_without_reranking() -> None:
    """Later turns resolve a pinned bundle rather than silently reranking it."""
    references = [("user-1", "memory-1")]
    continuity = SimpleNamespace(
        async_get_memory_bundle=AsyncMock(return_value=references),
        async_set_memory_bundle=AsyncMock(),
    )
    memory = SimpleNamespace(async_get_many=AsyncMock(return_value=["memory"]))
    rank = AsyncMock()
    agent = _MemoryRetrievalHarness(
        memory=memory,
        continuity=continuity,
        scopes=["user-1"],
        retrieve_limit=3,
        rank=rank,
    )
    token = conversation_module._ACTIVE_MEMORY_SESSION.set(("session-1", 30))
    try:
        result = await agent._async_retrieve_memories(object(), "same topic")
    finally:
        conversation_module._ACTIVE_MEMORY_SESSION.reset(token)

    assert result == ["memory"]
    rank.assert_not_awaited()
    continuity.async_set_memory_bundle.assert_not_awaited()
    memory.async_get_many.assert_awaited_once_with(references, ["user-1"])


@pytest.mark.asyncio
async def test_memory_retrieval_failure_is_isolated() -> None:
    """Continuity/storage failure must not take down the conversation turn."""
    continuity = SimpleNamespace(
        async_get_memory_bundle=AsyncMock(side_effect=RuntimeError("store unavailable"))
    )
    agent = SimpleNamespace(
        _memory=object(),
        _continuity=continuity,
        subentry=SimpleNamespace(data={CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 2}),
        _current_readable_memory_scope_ids=lambda _context: ["user-1"],
    )
    token = conversation_module._ACTIVE_MEMORY_SESSION.set(("session-1", 30))
    try:
        result = await Agent._async_select_memories(agent, object(), "hello")
    finally:
        conversation_module._ACTIVE_MEMORY_SESSION.reset(token)

    assert result == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "scopes", "embedding", "expected_scope", "expected_hybrid"),
    [
        ("lexical", ["user-1"], None, "user-1", False),
        (
            MEMORY_RETRIEVAL_HYBRID,
            ["user-1", "household"],
            [0.2, 0.4],
            ["user-1", "household"],
            True,
        ),
        (MEMORY_RETRIEVAL_HYBRID, ["user-1"], None, ["user-1"], False),
    ],
)
async def test_memory_search_preserves_lexical_and_hybrid_semantics(
    mode: str,
    scopes: list[str],
    embedding: list[float] | None,
    expected_scope: str | list[str],
    expected_hybrid: bool,
) -> None:
    """Search scope shape and Hybrid activation follow the configured retrieval mode."""
    prepare = AsyncMock(return_value=embedding)
    search = AsyncMock(return_value=["result"])
    memory = SimpleNamespace(
        async_prepare_hybrid=prepare,
        async_search=search,
    )
    agent = SimpleNamespace(
        _memory=memory,
        subentry=SimpleNamespace(data={CONF_MEMORY_RETRIEVAL_MODE: mode}),
    )

    result = await Agent._async_search_memories(
        agent,
        scopes,
        "boiler pressure",
        4,
        "fact",
    )

    assert result == ["result"]
    if mode == MEMORY_RETRIEVAL_HYBRID:
        prepare.assert_awaited_once_with(scopes, "boiler pressure")
    else:
        prepare.assert_not_awaited()
    search.assert_awaited_once_with(
        expected_scope,
        "boiler pressure",
        "fact",
        4,
        query_embedding=embedding,
        hybrid=expected_hybrid,
    )


def test_live_guest_policy_fails_closed_when_tool_parsing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed configured-tool snapshot must not bypass guest-policy resolution."""
    expected = object()
    resolver = Mock(return_value=expected)
    monkeypatch.setattr(conversation_module, "resolve_guest_policy", resolver)
    agent = SimpleNamespace(
        hass=object(),
        subentry=SimpleNamespace(data={"guest": "config"}),
        _guest_mode=object(),
        _get_configured_function_tools=Mock(side_effect=RuntimeError("bad config")),
    )

    result = Agent._resolve_live_guest_policy(agent)

    assert result is expected
    resolver.assert_called_once_with(
        agent.hass,
        agent.subentry.data,
        agent._guest_mode,
        [],
    )


def test_mid_request_guest_activation_is_pinned_for_remainder_of_turn() -> None:
    """A live restriction cannot later be relaxed within the same request."""
    live_policy = SimpleNamespace(guest_active=True)
    agent = SimpleNamespace(_resolve_live_guest_policy=lambda: live_policy)
    token = conversation_module._ACTIVE_GUEST_POLICY.set(None)
    try:
        result = Agent._effective_guest_policy(agent)
        pinned = conversation_module._ACTIVE_GUEST_POLICY.get()
    finally:
        conversation_module._ACTIVE_GUEST_POLICY.reset(token)

    assert result is live_policy
    assert pinned is live_policy


def test_guest_tool_group_filter_removes_disallowed_and_orphaned_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guest filtering must keep only permitted tools and valid group membership."""
    monkeypatch.setattr(
        conversation_module,
        "is_ha_tool",
        lambda tool: tool.get("function", {}).get("type") == "ha_llm",
    )
    tools = [
        {"spec": {"name": "allowed"}, "function": {"type": "script"}},
        {"spec": {"name": "policy_blocked"}, "function": {"type": "script"}},
        {"spec": {"name": "group_blocked"}, "function": {"type": "script"}},
        {"spec": {"name": "ha_owned"}, "function": {"type": "ha_llm"}},
        {"spec": {"name": "ungrouped"}, "function": {"type": "script"}},
    ]
    groups = [
        {
            "id": "guest-group",
            "guest_allowed": True,
            "functions": ["allowed", "policy_blocked"],
        },
        {
            "id": "private-group",
            "guest_allowed": False,
            "functions": ["group_blocked"],
        },
    ]
    policy = SimpleNamespace(
        guest_active=True,
        legacy_function_flags=True,
        allows_configured_tool=lambda name: name != "policy_blocked",
    )

    filtered_tools, filtered_groups = Agent._filter_guest_tools_and_groups(
        tools, groups, policy
    )

    assert [tool["spec"]["name"] for tool in filtered_tools] == [
        "allowed",
        "ungrouped",
    ]
    assert filtered_groups == [
        {
            "id": "guest-group",
            "guest_allowed": True,
            "functions": ["allowed"],
        }
    ]


@pytest.mark.parametrize(
    ("value", "control", "expected"),
    [
        ({"area_id": "kitchen"}, False, False),
        ({"target": {"device_ids": ["device-1"]}}, True, False),
        ({"entity_id": "light.allowed,sensor.allowed"}, False, True),
        ({"entity_ids": "light.allowed,light.denied"}, False, False),
        ({"entity_id": ["light.allowed"]}, False, True),
        (
            [
                {"entity_id": "light.allowed"},
                {"statistic_id": "sensor.allowed"},
            ],
            False,
            True,
        ),
        ({"entity_id": "sensor.allowed"}, True, False),
        ({"entity_id": "light.allowed"}, True, True),
    ],
)
def test_guest_argument_filter_is_recursive_and_fail_closed(
    value: object,
    control: bool,
    expected: bool,
) -> None:
    """Nested/broad selectors and entity lists must obey the correct guest capability."""
    policy = SimpleNamespace(
        allows_entity_read=lambda entity_id: (
            entity_id in {"light.allowed", "sensor.allowed"}
        ),
        allows_entity_control=lambda entity_id: entity_id == "light.allowed",
    )

    assert Agent._guest_arguments_allowed(value, policy, control=control) is expected
