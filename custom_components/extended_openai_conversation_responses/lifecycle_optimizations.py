"""Low-risk lifecycle and hot-path optimizations for conversation agents."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, cast

_TEMPORARY_MEMORY_PREFETCH: ContextVar[asyncio.Task[Any] | None] = ContextVar(
    "extended_openai_temporary_memory_prefetch", default=None
)
_INSTALLED = False


def install_lifecycle_optimizations() -> None:
    """Install continuity fields in debug summaries."""
    global _INSTALLED
    if _INSTALLED:
        return

    _install_debug_summary_fields()
    _INSTALLED = True


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
