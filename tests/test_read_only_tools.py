"""Tests for integration-owned read-only classification."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    archive_tools,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    build_loader_tool,
)
from custom_components.extended_openai_conversation_responses.function_tool_policy import (
    CONTINUE_CONVERSATION_TOOL_NAME,
    RESERVED_FUNCTION_TOOL_NAMES,
    RUNTIME_FUNCTION_TOOL_TYPES,
    is_integration_owned_read_only_tool,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    guest_mode_restrict_tool,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    knowledge_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from custom_components.extended_openai_conversation_responses.request import (
    START_FRESH_CONVERSATION_TOOL,
)
from custom_components.extended_openai_conversation_responses.scheduled_action_tools import (
    scheduled_action_tools,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    temporary_memory_tools,
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
        (_tool("get_history", "native", native_name="execute_service"), False),
        (_tool("knowledge_search", "rest", operation="search"), False),
        (_tool("unknown", "knowledge", operation="unknown"), False),
        (_tool("load_function_groups", "function_group_loader"), False),
        (_tool("custom_rest", "rest"), False),
    ],
)
def test_integration_owned_read_only_classification_is_explicit(
    tool: dict, expected: bool
) -> None:
    """Classify integration implementations with proven read-only semantics."""
    assert is_integration_owned_read_only_tool(tool) is expected


def test_generated_tool_definitions_match_runtime_policy() -> None:
    tools = [
        *archive_tools(),
        *knowledge_tools(),
        *memory_tools(),
        *temporary_memory_tools(),
        *scheduled_action_tools(),
        guest_mode_restrict_tool(),
        build_loader_tool([]),
        START_FRESH_CONVERSATION_TOOL,
    ]
    names = {tool["spec"]["name"] for tool in tools}
    assert len(names) == len(tools)
    assert RESERVED_FUNCTION_TOOL_NAMES == names | {CONTINUE_CONVERSATION_TOOL_NAME}
    assert RUNTIME_FUNCTION_TOOL_TYPES == {tool["function"]["type"] for tool in tools}
    assert {
        tool["spec"]["name"]
        for tool in tools
        if is_integration_owned_read_only_tool(tool)
    } == {
        "conversation_get",
        "conversation_search",
        "knowledge_get",
        "knowledge_list",
        "knowledge_search",
        "list_scheduled_actions",
        "memory_list",
        "memory_search",
    }
