"""Focused residual coverage for function group runtime composition."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
    function_tool_runtime_scope,
)


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": f"Use {name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def test_active_runtime_without_predicate_preserves_caller_predicate() -> None:
    """A request scope without a predicate must not discard caller filtering."""
    tools = [_tool("available"), _tool("blocked")]

    with function_tool_runtime_scope():
        assembly = assemble_function_tools(
            tools,
            [],
            set(),
            tool_available=lambda tool: tool["spec"]["name"] != "blocked",
        )

    assert [tool["spec"]["name"] for tool in assembly.tools] == ["available"]
