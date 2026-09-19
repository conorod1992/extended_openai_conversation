"""Focused coverage for lifecycle optimization safety and fallback paths."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    lifecycle_optimizations as lifecycle,
)


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
            temporary_memory_ownership as ownership,
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
