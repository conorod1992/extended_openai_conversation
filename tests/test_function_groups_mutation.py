"""Behavioural contracts for the selective Function Groups mutation campaign.

These tests exercise only deterministic group availability, schema assembly and
load-request behaviour. They deliberately do not cover management CRUD,
provider orchestration, execution, expiry timing or diagnostic wording.
"""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
)


def _tool(name: str, *, enabled: bool = True) -> dict:
    tool = {
        "spec": {
            "name": name,
            "description": f"Use {name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    if not enabled:
        tool["enabled"] = False
    return tool


def _group(
    group_id: str,
    functions: list[str],
    loading_mode: str = "on_demand",
    *,
    enabled: bool = True,
) -> dict:
    return {
        "id": group_id,
        "name": group_id.replace("_", " ").title(),
        "description": f"Capabilities for {group_id}",
        "loading_mode": loading_mode,
        "functions": functions,
        "enabled": enabled,
    }


def _names(assembly) -> list[str]:
    return [tool["spec"]["name"] for tool in assembly.tools]


def test_assembly_keeps_ungrouped_and_always_tools_but_withholds_on_demand() -> None:
    tools = [_tool("general"), _tool("calendar"), _tool("remind")]
    groups = [
        _group("calendar", ["calendar"], "always"),
        _group("reminders", ["remind"]),
    ]

    assembly = assemble_function_tools(tools, groups, set())

    assert _names(assembly) == ["general", "calendar", "load_function_groups"]
    loader = assembly.tools[-1]
    assert loader["spec"]["parameters"]["properties"]["groups"]["items"]["enum"] == [
        "reminders"
    ]


def test_loading_on_demand_group_exposes_its_members_and_removes_empty_loader() -> None:
    tools = [_tool("general"), _tool("create_reminder"), _tool("update_reminder")]
    groups = [_group("reminders", ["create_reminder", "update_reminder"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    result = load_function_groups(session, ["reminders"], groups, tools)

    assert result["status"] == "success"
    assert result["loaded"] == ["reminders"]
    assert session.loaded_group_ids == {"reminders"}
    assert _names(assemble_function_tools(tools, groups, session.loaded_group_ids)) == [
        "general",
        "create_reminder",
        "update_reminder",
    ]


def test_disabled_group_is_hidden_not_loadable_and_clears_stale_loaded_state() -> None:
    tool = _tool("remind")
    group = _group("reminders", ["remind"], enabled=False)
    loaded = {"reminders"}

    assembly = assemble_function_tools([tool], [group], loaded)

    assert assembly.tools == []
    assert loaded == set()
    result = load_function_groups(
        FunctionGroupRuntime().begin("conversation:one", 30),
        ["reminders"],
        [group],
        [tool],
    )
    assert result["status"] == "error"
    assert result["loaded"] == []


def test_individually_unavailable_member_is_omitted_without_hiding_viable_group() -> None:
    available = _tool("create_reminder")
    unavailable = _tool("delete_reminder", enabled=False)
    tools = [available, unavailable]
    groups = [_group("reminders", ["create_reminder", "delete_reminder"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    before = assemble_function_tools(tools, groups, session.loaded_group_ids)
    assert _names(before) == ["load_function_groups"]

    assert load_function_groups(session, ["reminders"], groups, tools)["loaded"] == [
        "reminders"
    ]
    after = assemble_function_tools(tools, groups, session.loaded_group_ids)
    assert _names(after) == ["create_reminder"]


def test_loaded_group_must_be_loaded_again_after_losing_all_available_members() -> None:
    tool = _tool("remind")
    groups = [_group("reminders", ["remind"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    assert load_function_groups(session, ["reminders"], groups, [tool])["loaded"] == [
        "reminders"
    ]

    tool["enabled"] = False
    assert assemble_function_tools([tool], groups, session.loaded_group_ids).tools == []
    assert session.loaded_group_ids == set()

    tool.pop("enabled")
    available_again = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert _names(available_again) == ["load_function_groups"]
    assert "remind" not in _names(available_again)


def test_group_loader_support_only_controls_on_demand_groups() -> None:
    tools = [_tool("general"), _tool("calendar"), _tool("remind")]
    groups = [
        _group("calendar", ["calendar"], "always"),
        _group("reminders", ["remind"]),
    ]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    assembly = assemble_function_tools(
        tools,
        groups,
        session.loaded_group_ids,
        group_loader_supported=False,
    )

    assert _names(assembly) == ["general", "calendar"]
    assert (
        load_function_groups(
            session,
            ["reminders"],
            groups,
            tools,
            group_loader_supported=False,
        )["status"]
        == "error"
    )
    assert session.loaded_group_ids == set()


def test_function_tool_support_disables_grouped_and_ungrouped_configured_tools() -> None:
    tools = [_tool("general"), _tool("calendar"), _tool("remind")]
    groups = [
        _group("calendar", ["calendar"], "always"),
        _group("reminders", ["remind"]),
    ]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    assembly = assemble_function_tools(
        tools,
        groups,
        session.loaded_group_ids,
        function_tools_supported=False,
    )

    assert assembly.tools == []
    assert (
        load_function_groups(
            session,
            ["reminders"],
            groups,
            tools,
            function_tools_supported=False,
        )["status"]
        == "error"
    )
    assert session.loaded_group_ids == set()


def test_runtime_predicate_exposes_only_available_members_and_keeps_group_loadable() -> None:
    tools = [_tool("allowed"), _tool("blocked")]
    groups = [_group("mixed", ["allowed", "blocked"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    def available(tool: dict) -> bool:
        return tool["spec"]["name"] == "allowed"

    before = assemble_function_tools(
        tools, groups, session.loaded_group_ids, tool_available=available
    )
    assert _names(before) == ["load_function_groups"]
    assert load_function_groups(
        session, ["mixed"], groups, tools, tool_available=available
    )["loaded"] == ["mixed"]
    after = assemble_function_tools(
        tools, groups, session.loaded_group_ids, tool_available=available
    )
    assert _names(after) == ["allowed"]


@pytest.mark.parametrize(
    "requested",
    [None, "reminders", [], [1], ["reminders", 1]],
)
def test_malformed_load_requests_fail_without_changing_loaded_state(requested) -> None:
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    groups = [_group("reminders", ["remind"])]

    result = load_function_groups(session, requested, groups, [_tool("remind")])

    assert result["status"] == "error"
    assert session.loaded_group_ids == set()


def test_mixed_valid_and_unknown_load_request_is_partial_and_loads_only_valid_group() -> None:
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    groups = [
        _group("reminders", ["remind"]),
        _group("calendar", ["calendar"], "always"),
    ]
    tools = [_tool("remind"), _tool("calendar")]

    result = load_function_groups(
        session, ["reminders", "calendar", "missing"], groups, tools
    )

    assert result["status"] == "partial"
    assert result["loaded"] == ["reminders"]
    assert result["already_available"] == ["calendar"]
    assert result["unknown"] == ["missing"]
    assert session.loaded_group_ids == {"reminders"}
