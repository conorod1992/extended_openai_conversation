"""Targeted regression tests for the last meaningful pytest coverage gaps."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope

Agent = conversation_module.ExtendedOpenAIAgentEntity


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        _memory=None,
        _temporary_memory=None,
        subentry=SimpleNamespace(data={"temporary_memory": "enabled"}),
        _effective_guest_policy=GuestCapabilityPolicy.unrestricted,
    )


async def test_persistent_memory_revalidation_drops_records_when_scope_is_revoked() -> None:
    """Selected memories must not survive a later loss of readable scope."""
    store = SimpleNamespace(async_get_many=AsyncMock())
    agent = _agent()
    agent._memory = store
    agent._current_readable_memory_scope_ids = Mock(return_value=[])
    records = [
        SimpleNamespace(user_id="alice", memory_id="memory-1"),
        SimpleNamespace(user_id="alice", memory_id="memory-2"),
    ]

    result = await Agent._async_revalidate_retrieved_memories(
        agent, object(), records
    )

    assert result == []
    store.async_get_many.assert_not_awaited()


async def test_persistent_memory_revalidation_uses_latest_scope_and_exact_ids() -> None:
    """Revalidation must resolve the selected IDs against current permissions."""
    expected = [SimpleNamespace(user_id="alice", memory_id="memory-2")]
    store = SimpleNamespace(async_get_many=AsyncMock(return_value=expected))
    agent = _agent()
    agent._memory = store
    agent._current_readable_memory_scope_ids = Mock(
        return_value=["user:alice", "shared:household"]
    )
    records = [
        SimpleNamespace(user_id="alice", memory_id="memory-1"),
        SimpleNamespace(user_id="alice", memory_id="memory-2"),
    ]

    result = await Agent._async_revalidate_retrieved_memories(
        agent, object(), records
    )

    assert result is expected
    store.async_get_many.assert_awaited_once_with(
        [("alice", "memory-1"), ("alice", "memory-2")],
        ["user:alice", "shared:household"],
    )


def test_temporary_memory_revalidation_drops_records_without_live_request_scope(
    monkeypatch,
) -> None:
    """Prefetched temporary memories cannot outlive their request scope."""
    records = [object()]
    agent = _agent()
    selector = Mock(return_value=["should-not-be-used"])
    monkeypatch.setattr(
        conversation_module.TemporaryMemory,
        "select_active_snapshot",
        selector,
    )

    active_scope = conversation_module._ACTIVE_SCOPE.set(
        user_scope("alice", source="test")
    )
    temporary_scope = conversation_module._ACTIVE_TEMPORARY_SCOPE.set(None)
    try:
        assert Agent._revalidate_temporary_memory_expiry(agent, records) == []
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(temporary_scope)
        conversation_module._ACTIVE_SCOPE.reset(active_scope)

    selector.assert_not_called()


def test_temporary_memory_revalidation_drops_records_without_retained_owner(
    monkeypatch,
) -> None:
    """A request scope alone is insufficient after retained ownership disappears."""
    records = [object()]
    agent = _agent()
    selector = Mock(return_value=["should-not-be-used"])
    monkeypatch.setattr(
        conversation_module.TemporaryMemory,
        "select_active_snapshot",
        selector,
    )

    active_scope = conversation_module._ACTIVE_SCOPE.set(None)
    temporary_scope = conversation_module._ACTIVE_TEMPORARY_SCOPE.set("request:one")
    try:
        assert Agent._revalidate_temporary_memory_expiry(agent, records) == []
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(temporary_scope)
        conversation_module._ACTIVE_SCOPE.reset(active_scope)

    selector.assert_not_called()


def test_temporary_memory_revalidation_filters_against_current_scope(
    monkeypatch,
) -> None:
    """Live request ownership is passed to the expiry/scope filter before prompting."""
    records = [object()]
    expected = [object()]
    agent = _agent()
    selector = Mock(return_value=expected)
    monkeypatch.setattr(
        conversation_module.TemporaryMemory,
        "select_active_snapshot",
        selector,
    )

    active_scope = conversation_module._ACTIVE_SCOPE.set(
        user_scope("alice", source="test")
    )
    temporary_scope = conversation_module._ACTIVE_TEMPORARY_SCOPE.set("request:one")
    try:
        result = Agent._revalidate_temporary_memory_expiry(agent, records)
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(temporary_scope)
        conversation_module._ACTIVE_SCOPE.reset(active_scope)

    assert result is expected
    selector.assert_called_once()
    assert selector.call_args.args[0] is records
    assert selector.call_args.args[1] == "request:one"
