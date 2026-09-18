"""Low-risk lifecycle and hot-path optimizations for conversation agents."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from typing import Any, cast

_TEMPORARY_MEMORY_PREFETCH: ContextVar[asyncio.Task[Any] | None] = ContextVar(
    "extended_openai_temporary_memory_prefetch", default=None
)
_INSTALLED = False


def install_lifecycle_optimizations() -> None:
    """Install memory prefetch and debug summary optimizations."""
    global _INSTALLED
    if _INSTALLED:
        return

    _install_memory_prefetch()
    _install_debug_summary_fields()
    _INSTALLED = True


def _install_memory_prefetch() -> None:
    """Overlap independent persistent- and temporary-memory retrieval."""
    from . import conversation as conversation_module
    from .conversation import ExtendedOpenAIAgentEntity
    from .temporary_memory_ownership import (
        _ACTIVE_OWNER_SCOPE_ID,
        _owner_from_resolved_scope,
    )

    agent_type: Any = ExtendedOpenAIAgentEntity
    original_retrieve_memories = agent_type._async_retrieve_memories
    original_retrieve_temporary = agent_type._async_retrieve_temporary_memories

    async def async_retrieve_memories(agent: Any, *args: Any, **kwargs: Any) -> Any:
        existing = _TEMPORARY_MEMORY_PREFETCH.get()
        task = existing
        if task is None and getattr(agent, "_temporary_memory", None) is not None:
            # create_task copies the current Context. Bind the resolved retained owner
            # before spawning the prefetch so an ownership wrapper installed after
            # this optimization cannot be bypassed by the captured retrieval callable.
            owner = _owner_from_resolved_scope(conversation_module._ACTIVE_SCOPE.get())
            owner_token = (
                _ACTIVE_OWNER_SCOPE_ID.set(owner) if owner is not None else None
            )
            try:
                task = asyncio.create_task(original_retrieve_temporary(agent))
            finally:
                if owner_token is not None:
                    _ACTIVE_OWNER_SCOPE_ID.reset(owner_token)
            _TEMPORARY_MEMORY_PREFETCH.set(task)
        try:
            return await original_retrieve_memories(agent, *args, **kwargs)
        except BaseException:
            if existing is None and task is not None:
                task.cancel()
                with suppress(BaseException):
                    await task
                _TEMPORARY_MEMORY_PREFETCH.set(None)
            raise

    async def async_retrieve_temporary(agent: Any, *args: Any, **kwargs: Any) -> Any:
        task = _TEMPORARY_MEMORY_PREFETCH.get()
        if task is None:
            return await original_retrieve_temporary(agent, *args, **kwargs)
        _TEMPORARY_MEMORY_PREFETCH.set(None)
        return await task

    agent_type._async_retrieve_memories = async_retrieve_memories
    agent_type._async_retrieve_temporary_memories = async_retrieve_temporary


def _install_debug_summary_fields() -> None:
    """Expose enough continuity metadata to label debug rows unambiguously."""
    from .debug import DebugTrace

    trace_type: Any = DebugTrace
    original_summary = trace_type.summary

    def summary(trace: Any) -> dict[str, Any]:
        result = cast(dict[str, Any], original_summary(trace))
        result["continuity_mode"] = trace.continuity.get("mode")
        result["restored_history_items"] = int(
            trace.continuity.get("restored_history_items") or 0
        )
        return result

    trace_type.summary = summary
