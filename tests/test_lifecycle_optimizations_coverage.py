"""Focused coverage for lifecycle optimization safety and fallback paths."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    lifecycle_optimizations as lifecycle,
)


def _install_memory_wrappers(
    monkeypatch: pytest.MonkeyPatch,
    retrieve_memories: Any,
    retrieve_temporary: Any,
    owner: Any,
) -> tuple[Any, Any, Any]:
    """Install memory wrappers around controlled retrieval stand-ins."""
    from custom_components.extended_openai_conversation_responses import (
        temporary_memory_ownership as ownership,
    )
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_async_retrieve_memories", retrieve_memories
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_retrieve_temporary_memories",
        retrieve_temporary,
    )
    monkeypatch.setattr(ownership, "_owner_from_resolved_scope", lambda _scope: owner)
    lifecycle._install_memory_prefetch()
    return (
        ExtendedOpenAIAgentEntity._async_retrieve_memories,
        ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories,
        ownership,
    )


async def test_memory_prefetch_binds_owner_and_is_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary retrieval overlaps persistent retrieval under the resolved owner."""
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
        assert args == ("query",)
        assert kwargs == {"limit": 2}
        return ["persistent"]

    wrapped_memories, wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, "owner-1"
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await wrapped_memories(agent, "query", limit=2) == ["persistent"]
        task = lifecycle._TEMPORARY_MEMORY_PREFETCH.get()
        assert task is not None
        assert observed_owner == ["owner-1"]

        release.set()
        assert await wrapped_temporary(agent) == ["temporary"]
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

    async def _memories(_agent: Any) -> None:
        await started.wait()
        raise RuntimeError("memory lookup failed")

    wrapped_memories, _wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, None
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        with pytest.raises(RuntimeError, match="memory lookup failed"):
            await wrapped_memories(agent)
        assert cancelled.is_set()
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)


async def test_temporary_memory_wrapper_uses_direct_and_existing_task_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct retrieval is preserved, while an existing prefetch is reused exactly once."""
    direct_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _temporary(_agent: Any, *args: Any, **kwargs: Any) -> Any:
        direct_calls.append((args, kwargs))
        return "direct"

    async def _memories(_agent: Any) -> str:
        return "persistent"

    wrapped_memories, wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, None
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await wrapped_temporary(agent, "arg", key="value") == "direct"
        assert direct_calls == [(("arg",), {"key": "value"})]

        existing = asyncio.create_task(asyncio.sleep(0, result="prefetched"))
        lifecycle._TEMPORARY_MEMORY_PREFETCH.set(existing)
        assert await wrapped_memories(agent) == "persistent"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is existing
        assert await wrapped_temporary(agent) == "prefetched"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
        assert direct_calls == [(("arg",), {"key": "value"})]
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)
