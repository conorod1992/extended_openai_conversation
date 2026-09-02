"""Tests for automatic lazy loading of known specialist built-in tools."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
)


def _native_tool(spec_name: str, implementation: str, *, enabled: bool = True) -> dict:
    tool = {
        "spec": {
            "name": spec_name,
            "description": f"Use {spec_name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": implementation},
    }
    if not enabled:
        tool["enabled"] = False
    return tool


def _group(
    group_id: str,
    functions: list[str],
    loading_mode: str = "on_demand",
) -> dict:
    return {
        "id": group_id,
        "name": group_id.replace("_", " ").title(),
        "description": f"Capabilities for {group_id}",
        "loading_mode": loading_mode,
        "functions": functions,
    }


def _loader_group_ids(assembly) -> list[str]:
    loader = next(
        tool for tool in assembly.tools if tool["spec"]["name"] == "load_function_groups"
    )
    return loader["spec"]["parameters"]["properties"]["groups"]["items"]["enum"]


def test_ungrouped_specialist_builtins_are_lazy_by_default() -> None:
    tools = [
        _native_tool("execute_services", "execute_service"),
        _native_tool("history_reader", "get_history"),
        _native_tool("stats_reader", "get_statistics"),
        _native_tool("energy_reader", "get_energy"),
        _native_tool("automation_writer", "add_automation"),
        _native_tool("broadcast_sender", "send_broadcast"),
    ]

    assembly = assemble_function_tools(tools, [], set())

    assert [tool["spec"]["name"] for tool in assembly.tools] == [
        "execute_services",
        "load_function_groups",
    ]
    assert _loader_group_ids(assembly) == [
        "auto_history",
        "auto_automation",
        "auto_broadcast",
    ]
    assert assembly.configured_count == 6
    assert assembly.configured_schemas_sent == 1
    assert assembly.available_on_demand_groups == 3


def test_loading_automatic_history_group_restores_only_history_tools() -> None:
    tools = [
        _native_tool("execute_services", "execute_service"),
        _native_tool("history_reader", "get_history"),
        _native_tool("stats_reader", "get_statistics"),
        _native_tool("energy_reader", "get_energy"),
        _native_tool("automation_writer", "add_automation"),
        _native_tool("broadcast_sender", "send_broadcast"),
    ]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    loaded = load_function_groups(session, ["auto_history"], [], tools)
    assembly = assemble_function_tools(tools, [], session.loaded_group_ids)

    assert loaded["status"] == "success"
    assert loaded["loaded"] == ["auto_history"]
    assert [tool["spec"]["name"] for tool in assembly.tools] == [
        "execute_services",
        "history_reader",
        "stats_reader",
        "energy_reader",
        "load_function_groups",
    ]
    assert _loader_group_ids(assembly) == ["auto_automation", "auto_broadcast"]


def test_explicit_user_groups_override_automatic_grouping() -> None:
    tools = [
        _native_tool("history_reader", "get_history"),
        _native_tool("stats_reader", "get_statistics"),
        _native_tool("automation_writer", "add_automation"),
    ]
    groups = [
        _group("history_always", ["history_reader"], "always"),
        _group("custom_automation", ["automation_writer"]),
    ]

    assembly = assemble_function_tools(tools, groups, set())

    assert [tool["spec"]["name"] for tool in assembly.tools] == [
        "history_reader",
        "load_function_groups",
    ]
    assert _loader_group_ids(assembly) == ["custom_automation", "auto_history"]
    assert "auto_automation" not in _loader_group_ids(assembly)


def test_automatic_group_ids_avoid_user_group_collisions() -> None:
    tools = [
        _native_tool("general", "execute_service"),
        _native_tool("history_reader", "get_history"),
    ]
    groups = [_group("auto_history", ["general"], "always")]

    assembly = assemble_function_tools(tools, groups, set())

    assert [tool["spec"]["name"] for tool in assembly.tools] == [
        "general",
        "load_function_groups",
    ]
    assert _loader_group_ids(assembly) == ["auto_history_2"]


def test_disabled_specialist_does_not_create_loadable_group() -> None:
    tools = [_native_tool("history_reader", "get_history", enabled=False)]

    assembly = assemble_function_tools(tools, [], set())

    assert assembly.tools == []
    assert assembly.available_on_demand_groups == 0
