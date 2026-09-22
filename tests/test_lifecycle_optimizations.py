"""Regression tests for lifecycle and request-path optimizations."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses import conversation as lifecycle
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SERVICE_TIER,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.debug import DebugTrace
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
    UsageRun,
)
from homeassistant.components import conversation
from homeassistant.util import dt as dt_util


class DelayedStorage:
    """In-memory Store stand-in with Home Assistant delayed-save semantics."""

    def __init__(self) -> None:
        self.data = None
        self.immediate_saves = 0
        self.delayed: list[tuple[object, float]] = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.immediate_saves += 1
        self.data = deepcopy(data)

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.delayed.append((data_func, delay))

    def latest_delayed_data(self):
        return deepcopy(self.delayed[-1][0]()) if self.delayed else None


async def _usage_manager():
    totals = DelayedStorage()
    daily = DelayedStorage()
    details = DelayedStorage()
    manager = UsageManager(
        totals,
        daily,
        details,
        agent_subentry_id="agent",
    )
    await manager.async_initialize()
    return manager, totals, daily, details


async def test_routine_usage_writes_are_delayed_but_explicit_clear_is_durable() -> None:
    """Ordinary turns leave the response path while explicit maintenance still saves."""
    manager, totals, daily, details = await _usage_manager()

    async with manager.async_run(home_assistant_conversation_id="conversation-a"):
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
        )

    assert totals.immediate_saves == 0
    assert daily.immediate_saves == 0
    assert details.immediate_saves == 0
    assert totals.delayed
    assert daily.delayed
    assert details.delayed
    assert totals.latest_delayed_data()["total_tokens"] == 7
    assert len(details.latest_delayed_data()["runs"]) == 1

    result = await manager.async_clear_details(confirm=True)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert details.immediate_saves == 1
    assert details.data == {"requests": [], "runs": []}


async def test_usage_retention_is_reapplied_lazily_during_long_uptime() -> None:
    """A new UTC day prunes stale detail rows without requiring a restart."""
    manager, _totals, _daily, details = await _usage_manager()
    old = (dt_util.utcnow() - timedelta(days=120)).isoformat()
    manager.requests = [
        UsageRequest(
            request_id="old-request",
            run_id="old-run",
            timestamp=old,
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        )
    ]
    manager.runs = [
        UsageRun(
            run_id="old-run",
            started_at=old,
            completed_at=old,
            duration_ms=1,
            agent_subentry_id="agent",
            home_assistant_conversation_id="old-conversation",
            source_device_id=None,
        )
    ]
    manager.request_retention_days = 30
    manager.run_retention_days = 90
    manager._last_prune_date = "1900-01-01"

    await manager._async_prune_usage_if_due()
    task = manager._prune_task
    assert task is not None
    await task

    assert manager.requests == []
    assert manager.runs == []
    assert details.immediate_saves == 1
    assert details.data == {"requests": [], "runs": []}


async def test_ha_default_never_restores_history_between_distinct_ids() -> None:
    """Prompt-cache reuse must not be confused with Home Assistant chat continuity."""
    manager = ConversationContinuity("agent")
    scope = user_scope("user", source="test")

    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        scope,
        "kitchen",
        "conversation-a",
        30,
    )
    await manager.async_record_success(
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="private first conversation")],
    )
    second = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        scope,
        "kitchen",
        "conversation-b",
        30,
    )

    assert first.conversation_id == "conversation-a"
    assert second.conversation_id == "conversation-b"
    assert first.key is None and second.key is None
    assert first.history == [] and second.history == []
    assert first.resumed is False and second.resumed is False


def test_service_tier_request_respects_model_specific_options() -> None:
    """Standard remains universal while Flex is sent only for supported models."""
    defaults = agent_config.agent_config_defaults()
    assert defaults[CONF_SERVICE_TIER] == "default"

    missing = build_provider_request_snapshot({}, {"api_provider": "openai"})
    unsupported_flex = build_provider_request_snapshot(
        {CONF_SERVICE_TIER: "flex"}, {"api_provider": "openai"}
    )
    supported_flex = build_provider_request_snapshot(
        {"chat_model": "gpt-5.6-terra", CONF_SERVICE_TIER: "flex"},
        {"api_provider": "openai"},
    )
    assert missing.api_kwargs.get("service_tier") == "default"
    assert "service_tier" not in unsupported_flex.api_kwargs
    assert supported_flex.api_kwargs.get("service_tier") == "flex"


def test_debug_summary_exposes_mode_without_treating_cache_reuse_as_continuity() -> (
    None
):
    trace = DebugTrace(
        debug_id="debug",
        entry_id="entry",
        subentry_id="agent",
        started_at=dt_util.utcnow().isoformat(),
        user_input={},
        incoming_conversation_id="conversation-b",
    )
    trace.continuity = {
        "mode": CONVERSATION_CONTINUITY_HA_DEFAULT,
        "resolved_conversation_id": "conversation-b",
        "resumed": False,
        "restored_history_items": 0,
    }

    summary = trace.summary()

    assert summary["continuity_mode"] == CONVERSATION_CONTINUITY_HA_DEFAULT
    assert summary["restored_history_items"] == 0


def _retrieval_agent(monkeypatch, retrieve_memories, retrieve_temporary):
    from types import MethodType

    from custom_components.extended_openai_conversation_responses import conversation

    agent = object.__new__(conversation.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"temporary_memory": "enabled"})
    agent._temporary_memory = object()
    agent._async_select_memories = MethodType(retrieve_memories, agent)
    agent._async_load_temporary_memories = MethodType(retrieve_temporary, agent)
    agent._effective_guest_policy = lambda: SimpleNamespace(temporary_memory=True)
    monkeypatch.setattr(conversation, "memory_enabled", lambda _data: True)
    monkeypatch.setattr(
        conversation, "sync_memory_embedding_provider", lambda _agent: None
    )
    monkeypatch.setattr(
        conversation, "_owner_from_resolved_scope", lambda _scope: "user:alice"
    )
    monkeypatch.setattr(
        conversation, "_ACTIVE_TEMPORARY_SCOPE", SimpleNamespace(get=lambda: "session")
    )
    return agent


async def test_memory_prefetch_overlaps_and_is_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary retrieval overlaps persistent retrieval and is consumed once."""
    started = asyncio.Event()
    release = asyncio.Event()
    observed_owner: list[Any] = []

    async def _temporary(_agent: Any) -> list[str]:
        from custom_components.extended_openai_conversation_responses import (
            temporary_memory as ownership,
        )

        observed_owner.append(ownership._ACTIVE_OWNER_SCOPE_ID.get())
        started.set()
        await release.wait()
        return ["temporary"]

    async def _memories(_agent: Any, *args: Any, **kwargs: Any) -> list[str]:
        await started.wait()
        assert args == ("context", "query")
        assert kwargs == {}
        return ["persistent"]

    agent = _retrieval_agent(monkeypatch, _memories, _temporary)
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await agent._async_retrieve_memories("context", "query") == [
            "persistent"
        ]
        task = lifecycle._TEMPORARY_MEMORY_PREFETCH.get()
        assert task is not None
        assert observed_owner == [
            None
        ]  # Owner binding is inside the real loader, tested separately.

        release.set()
        assert await agent._async_retrieve_temporary_memories() == ["temporary"]
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        pending = lifecycle._TEMPORARY_MEMORY_PREFETCH.get()
        if pending is not None and not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)


async def test_memory_prefetch_is_cancelled_when_persistent_retrieval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed main retrieval must not leak its speculative temporary-memory task."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _temporary(_agent: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _memories(_agent: Any, *_args: Any) -> None:
        await started.wait()
        raise RuntimeError("memory lookup failed")

    agent = _retrieval_agent(monkeypatch, _memories, _temporary)
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        with pytest.raises(RuntimeError, match="memory lookup failed"):
            await agent._async_retrieve_memories("context", "query")
        assert cancelled.is_set()
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)


async def test_temporary_memory_owner_uses_direct_and_existing_task_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct retrieval is preserved, while an existing prefetch is reused exactly once."""
    direct_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _temporary(_agent: Any, *args: Any, **kwargs: Any) -> Any:
        direct_calls.append((args, kwargs))
        return "direct"

    async def _memories(_agent: Any, *_args: Any) -> str:
        return "persistent"

    agent = _retrieval_agent(monkeypatch, _memories, _temporary)
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await agent._async_retrieve_temporary_memories() == "direct"
        assert direct_calls == [((), {})]

        existing = asyncio.create_task(asyncio.sleep(0, result="prefetched"))
        lifecycle._TEMPORARY_MEMORY_PREFETCH.set(existing)
        assert await agent._async_retrieve_memories("context", "query") == "persistent"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is existing
        assert await agent._async_retrieve_temporary_memories() == "prefetched"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
        assert direct_calls == [((), {})]
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)
