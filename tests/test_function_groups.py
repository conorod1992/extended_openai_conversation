"""Tests for optional on-demand configured function groups."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_snapshot,
    normalize_agent_config,
    validate_function_groups,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
    reset_function_group_runtime,
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


def test_legacy_config_has_no_groups_and_keeps_all_tools(hass) -> None:
    config = normalize_agent_config({"functions": [_tool("one"), _tool("two")]})
    snapshot = agent_config_snapshot(config)
    assert snapshot["function_groups"] == []
    assembly = assemble_function_tools(snapshot["functions"], [], set())
    assert [tool["spec"]["name"] for tool in assembly.tools] == ["one", "two"]
    assert assembly.full_schemas_sent == 2


def test_always_and_on_demand_assembly_is_compact(hass) -> None:
    tools = [_tool("general"), _tool("create_reminder"), _tool("calendar")]
    groups = validate_function_groups(
        [
            _group("reminders", ["create_reminder"]),
            _group("calendar", ["calendar"], "always"),
        ],
        tools,
    )
    assembly = assemble_function_tools(tools, groups, set())
    names = [tool["spec"]["name"] for tool in assembly.tools]
    assert names == ["general", "calendar", "load_function_groups"]
    assert assembly.full_schemas_sent == 2
    assert assembly.available_on_demand_groups == 1
    loader_text = assembly.tools[-1]["spec"]["description"]
    assert "reminders" in loader_text
    assert "create_reminder" not in loader_text


def test_loader_accepts_one_or_multiple_groups_and_is_idempotent() -> None:
    runtime = FunctionGroupRuntime()
    session = runtime.begin("conversation:one", 30)
    groups = [_group("reminders", ["one"]), _group("calendar", ["two"])]
    first = load_function_groups(session, ["reminders"], groups)
    assert first["status"] == "success"
    assert first["loaded"] == ["reminders"]
    repeated = load_function_groups(session, ["reminders"], groups)
    assert repeated["status"] == "success"
    assert repeated["already_loaded"] == ["reminders"]
    multiple = load_function_groups(session, ["calendar", "reminders"], groups)
    assert multiple["loaded"] == ["calendar"]
    assert multiple["already_loaded"] == ["reminders"]


def test_unknown_and_always_available_group_results_are_deterministic() -> None:
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    groups = [_group("reminders", ["one"]), _group("general", ["two"], "always")]
    result = load_function_groups(session, ["missing", "general"], groups)
    assert result == {
        "status": "error",
        "loaded": [],
        "already_loaded": [],
        "already_available": ["general"],
        "unknown": ["missing"],
        "loadable_groups": ["reminders"],
    }


def test_loaded_groups_are_isolated_and_removed_groups_cannot_resurrect_tools() -> None:
    runtime = FunctionGroupRuntime()
    first = runtime.begin("conversation:first", 30)
    second = runtime.begin("conversation:second", 30)
    groups = [_group("reminders", ["remind"])]
    load_function_groups(first, ["reminders"], groups)
    assert runtime.begin("conversation:first", 30).loaded_group_ids == {"reminders"}
    assert "remind" in {
        tool["spec"]["name"]
        for tool in assemble_function_tools(
            [_tool("remind")], groups, first.loaded_group_ids
        ).tools
    }
    assert "remind" not in {
        tool["spec"]["name"]
        for tool in assemble_function_tools(
            [_tool("remind")], groups, second.loaded_group_ids
        ).tools
    }
    assemble_function_tools([_tool("remind")], [], first.loaded_group_ids)
    assert first.loaded_group_ids == set()


def test_expired_conversation_starts_with_no_loaded_groups() -> None:
    runtime = FunctionGroupRuntime()
    expired = runtime.begin("conversation:expired", 30)
    expired.loaded_group_ids.add("reminders")
    expired.last_active = 0
    fresh = runtime.begin("conversation:fresh", 30)
    assert fresh.loaded_group_ids == set()
    recreated = runtime.begin("conversation:expired", 30)
    assert recreated is not expired
    assert recreated.loaded_group_ids == set()


def test_agent_reload_and_multiple_agents_isolate_runtime(hass) -> None:
    first = reset_function_group_runtime(hass, "entry", "agent-one")
    second = reset_function_group_runtime(hass, "entry", "agent-two")
    first.begin("conversation:same", 30).loaded_group_ids.add("reminders")
    assert second.begin("conversation:same", 30).loaded_group_ids == set()
    reloaded = reset_function_group_runtime(hass, "entry", "agent-one")
    assert reloaded is not first
    assert reloaded.begin("conversation:same", 30).loaded_group_ids == set()


async def test_version_six_migration_adds_empty_groups_without_rewriting_tools(
    hass,
) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data={"functions": "[]\n"},
    )
    entry = SimpleNamespace(
        entry_id="entry",
        version=5,
        disabled_by=None,
        subentries={"agent": subentry},
    )
    hass.config_entries.async_entries.return_value = [entry]
    await async_migrate_integration(hass)
    migrated = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert migrated["functions"] == "[]\n"
    assert migrated["function_groups"] == []
    assert hass.config_entries.async_update_entry.call_args.kwargs["version"] == 6


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([_group("Bad ID", ["one"])], r"function_groups\[0\]\.id"),
        ([_group("one", ["one"], "sometimes")], "unsupported value"),
        ([_group("one", ["missing"])], "unknown function"),
        ([_group("one", ["one"]), _group("one", [])], "duplicate group ID"),
        (
            [_group("one", ["one"]), _group("two", ["one"])],
            "already assigned",
        ),
    ],
)
def test_group_validation_rejects_ambiguous_or_invalid_config(
    groups: list[dict], message: str
) -> None:
    with pytest.raises(AgentConfigError, match=message):
        validate_function_groups(groups, [_tool("one")])


def test_group_round_trip_and_reserved_loader_name(hass) -> None:
    config = normalize_agent_config(
        {
            "functions": [_tool("one")],
            "function_groups": [_group("tools", ["one"])],
        }
    )
    snapshot = agent_config_snapshot(config)
    assert snapshot["function_groups"] == [_group("tools", ["one"])]
    with pytest.raises(AgentConfigError, match="reserved"):
        normalize_agent_config(
            {
                "functions": [_tool("load_function_groups"), _tool("one")],
                "function_groups": [_group("tools", ["one"])],
            }
        )
