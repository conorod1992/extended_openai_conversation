"""Conservative concurrency for integration-owned read-only tool calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import llm

_PARALLEL_SAFE_OPERATIONS = frozenset(
    {
        ("memory", "search"),
        ("memory", "list"),
        ("knowledge", "search"),
        ("knowledge", "list"),
        ("knowledge", "get"),
        ("archive", "search"),
        ("archive", "get"),
    }
)

# These are user-exposable Function Tool presets, but their implementations are
# integration-owned and known to be read-only. Arbitrary configured Function Tools
# remain serial unless a future explicit opt-in is added.
_PARALLEL_SAFE_NATIVE_NAMES = frozenset(
    {
        "get_history",
        "get_energy",
        "get_statistics",
        "get_user_from_user_id",
    }
)

ResolvedToolCall = tuple[dict[str, Any], llm.ToolInput]
ToolExecutor = Callable[
    [dict[str, Any], llm.ToolInput],
    Awaitable[conversation.ToolResultContent],
]


def is_parallel_safe_integration_tool(tool: Mapping[str, Any]) -> bool:
    """Return whether the integration owns and guarantees this tool is read-only."""
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return False
    function_type = function.get("type")
    if function_type == "native":
        return function.get("name") in _PARALLEL_SAFE_NATIVE_NAMES
    return (function_type, function.get("operation")) in _PARALLEL_SAFE_OPERATIONS


def resolve_parallel_safe_batch(
    pending_tool_calls: Sequence[llm.ToolInput],
    function_tools_by_name: Mapping[str, dict[str, Any]],
) -> list[ResolvedToolCall] | None:
    """Resolve a provider batch only when every call is known parallel-safe."""
    if len(pending_tool_calls) < 2:
        return None
    resolved: list[ResolvedToolCall] = []
    for tool_input in pending_tool_calls:
        function_tool = function_tools_by_name.get(tool_input.tool_name)
        if function_tool is None or not is_parallel_safe_integration_tool(function_tool):
            return None
        resolved.append((function_tool, tool_input))
    return resolved


async def async_execute_parallel_safe_batch(
    resolved_calls: Sequence[ResolvedToolCall],
    executor: ToolExecutor,
) -> list[conversation.ToolResultContent]:
    """Execute one proven-safe batch concurrently while preserving result order.

    Tasks start together, but results are awaited in provider order. If an earlier
    call fails, later results are not surfaced, matching the serial model-visible
    failure order. Remaining read-only tasks are cancelled/collected before the
    exception propagates.
    """
    tasks = [
        asyncio.create_task(executor(function_tool, tool_input))
        for function_tool, tool_input in resolved_calls
    ]
    results: list[conversation.ToolResultContent] = []
    try:
        for task in tasks:
            results.append(await task)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return results
