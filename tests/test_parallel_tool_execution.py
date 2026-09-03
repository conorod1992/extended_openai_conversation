"""Tests for conservative integration-owned parallel tool execution."""

from __future__ import annotations

import asyncio

import pytest

from homeassistant.components import conversation
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.parallel_tool_execution import (
    async_execute_parallel_safe_batch,
    is_parallel_safe_integration_tool,
    resolve_parallel_safe_batch,
)


def _tool(
    name: str,
    function_type: str,
    *,
    operation: str | None = None,
    native_name: str | None = None,
) -> dict:
    function: dict[str, str] = {"type": function_type}
    if operation is not None:
        function["operation"] = operation
    if native_name is not None:
        function["name"] = native_name
    return {
        "spec": {"name": name, "parameters": {"type": "object"}},
        "function": function,
    }


def _call(name: str, call_id: str) -> llm.ToolInput:
    return llm.ToolInput(
        id=call_id,
        tool_name=name,
        tool_args={},
        external=True,
    )


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (_tool("memory_search", "memory", operation="search"), True),
        (_tool("memory_list", "memory", operation="list"), True),
        (_tool("knowledge_search", "knowledge", operation="search"), True),
        (_tool("knowledge_list", "knowledge", operation="list"), True),
        (_tool("knowledge_get", "knowledge", operation="get"), True),
        (_tool("conversation_search", "archive", operation="search"), True),
        (_tool("conversation_get", "archive", operation="get"), True),
        (_tool("history", "native", native_name="get_history"), True),
        (_tool("energy", "native", native_name="get_energy"), True),
        (_tool("statistics", "native", native_name="get_statistics"), True),
        (_tool("user", "native", native_name="get_user_from_user_id"), True),
        (_tool("memory_update", "memory", operation="update"), False),
        (_tool("temporary_memory_add", "temporary_memory", operation="add"), False),
        (_tool("conversation_private", "archive", operation="private"), False),
        (_tool("guest_mode_restrict", "guest_mode", operation="restrict"), False),
        (_tool("broadcast", "native", native_name="send_broadcast"), False),
        (_tool("service", "native", native_name="execute_service"), False),
        (_tool("custom_rest", "rest"), False),
    ],
)
def test_parallel_safe_classification_is_explicit(tool: dict, expected: bool) -> None:
    """Only integration implementations with proven read-only semantics are safe."""
    assert is_parallel_safe_integration_tool(tool) is expected


def test_batch_requires_multiple_known_safe_calls() -> None:
    """Unknown, custom, mutating, and single-call batches keep serial behavior."""
    safe = _tool("knowledge_search", "knowledge", operation="search")
    other_safe = _tool("history", "native", native_name="get_history")
    mutation = _tool("service", "native", native_name="execute_service")
    tools = {
        "knowledge_search": safe,
        "history": other_safe,
        "service": mutation,
    }

    assert resolve_parallel_safe_batch([_call("knowledge_search", "1")], tools) is None
    assert (
        resolve_parallel_safe_batch(
            [_call("knowledge_search", "1"), _call("missing", "2")], tools
        )
        is None
    )
    assert (
        resolve_parallel_safe_batch(
            [_call("knowledge_search", "1"), _call("service", "2")], tools
        )
        is None
    )
    resolved = resolve_parallel_safe_batch(
        [_call("knowledge_search", "1"), _call("history", "2")], tools
    )
    assert resolved is not None
    assert [tool_input.id for _, tool_input in resolved] == ["1", "2"]


@pytest.mark.asyncio
async def test_parallel_batch_overlaps_execution_and_preserves_provider_order() -> None:
    """Safe calls start together even when the second one finishes first."""
    calls = [
        (_tool("first", "knowledge", operation="search"), _call("first", "1")),
        (_tool("second", "knowledge", operation="list"), _call("second", "2")),
    ]
    both_started = asyncio.Event()
    started: list[str] = []

    async def execute(function_tool: dict, tool_input: llm.ToolInput):
        started.append(tool_input.id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        if tool_input.id == "1":
            await asyncio.sleep(0.02)
        return conversation.ToolResultContent(
            agent_id="test",
            tool_call_id=tool_input.id,
            tool_name=tool_input.tool_name,
            tool_result={"result": tool_input.id},
        )

    results = await async_execute_parallel_safe_batch(calls, execute)

    assert set(started) == {"1", "2"}
    assert [result.tool_call_id for result in results] == ["1", "2"]


@pytest.mark.asyncio
async def test_parallel_batch_preserves_first_failure_order() -> None:
    """An earlier failing read is still surfaced before later successful results."""
    calls = [
        (_tool("first", "knowledge", operation="search"), _call("first", "1")),
        (_tool("second", "knowledge", operation="list"), _call("second", "2")),
    ]
    both_started = asyncio.Event()
    started: list[str] = []

    async def execute(function_tool: dict, tool_input: llm.ToolInput):
        started.append(tool_input.id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        if tool_input.id == "1":
            raise RuntimeError("first failed")
        return conversation.ToolResultContent(
            agent_id="test",
            tool_call_id=tool_input.id,
            tool_name=tool_input.tool_name,
            tool_result={"result": tool_input.id},
        )

    with pytest.raises(RuntimeError, match="first failed"):
        await async_execute_parallel_safe_batch(calls, execute)

    assert set(started) == {"1", "2"}
