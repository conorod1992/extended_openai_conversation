"""Tests for optional on-demand configured function groups."""

from __future__ import annotations

import json
import time
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
from custom_components.extended_openai_conversation_responses.const import (
    CONFIG_ENTRY_VERSION,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    function_tool_runtime_scope,
    get_function_group_runtime,
    load_function_groups,
    remove_function_group_runtime,
    reset_function_group_runtime,
)
from custom_components.extended_openai_conversation_responses.function_tool_resolution import (
    latest_function_tool_for_execution,
)
from homeassistant.exceptions import HomeAssistantError


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


def test_legacy_config_has_no_groups_and_keeps_all_tools(hass) -> None:
    config = normalize_agent_config({"functions": [_tool("one"), _tool("two")]})
    snapshot = agent_config_snapshot(config)
    assert snapshot["function_groups"] == []
    assembly = assemble_function_tools(snapshot["functions"], [], set())
    assert [tool["spec"]["name"] for tool in assembly.tools] == ["one", "two"]
    assert assembly.configured_schemas_sent == 2


def test_legacy_group_without_enabled_state_defaults_enabled() -> None:
    legacy = _group("tools", ["one"])
    legacy.pop("enabled")
    assert validate_function_groups([legacy], [_tool("one")]) == [
        _group("tools", ["one"])
    ]


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
    assert assembly.configured_schemas_sent == 2
    assert assembly.available_on_demand_groups == 1
    loader_text = assembly.tools[-1]["spec"]["description"]
    assert "reminders" in loader_text
    assert "create_reminder" not in loader_text
    assert assembly.serialized_configured_schema_characters == sum(
        len(json.dumps(tool["spec"], separators=(",", ":")))
        for tool in assembly.tools[:-1]
    )
    loader_spec = assembly.tools[-1]["spec"]
    assert "strict" not in loader_spec
    assert loader_spec["parameters"] == {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {"type": "string", "enum": ["reminders"]},
            }
        },
        "required": ["groups"],
        "additionalProperties": False,
    }


def test_disabled_group_hides_members_and_preserves_individual_tool_state() -> None:
    enabled_tool = _tool("enabled")
    disabled_tool = {**_tool("disabled"), "enabled": False}
    group = _group("mixed", ["enabled", "disabled"], enabled=False)
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    session.loaded_group_ids.add("mixed")

    assembly = assemble_function_tools(
        [enabled_tool, disabled_tool], [group], session.loaded_group_ids
    )
    assert assembly.tools == []
    assert session.loaded_group_ids == set()
    assert enabled_tool.get("enabled", True) is True
    assert disabled_tool["enabled"] is False
    assert (
        load_function_groups(session, ["mixed"], [group], [enabled_tool])["status"]
        == "error"
    )

    group["enabled"] = True
    available = assemble_function_tools(
        [enabled_tool, disabled_tool], [group], session.loaded_group_ids
    )
    assert [tool["spec"]["name"] for tool in available.tools] == [
        "load_function_groups"
    ]
    assert load_function_groups(
        session, ["mixed"], [group], [enabled_tool, disabled_tool]
    )["loaded"] == ["mixed"]
    restored = assemble_function_tools(
        [enabled_tool, disabled_tool], [group], session.loaded_group_ids
    )
    assert [tool["spec"]["name"] for tool in restored.tools] == ["enabled"]
    assert disabled_tool["enabled"] is False


def test_disabled_tools_stay_grouped_but_are_excluded_from_effective_assembly() -> None:
    enabled = _tool("enabled")
    disabled = {**_tool("disabled"), "enabled": False}
    groups = [_group("mixed", ["enabled", "disabled"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    load_function_groups(session, ["mixed"], groups, [enabled, disabled])
    assembly = assemble_function_tools(
        [enabled, disabled], groups, session.loaded_group_ids
    )
    assert groups[0]["functions"] == ["enabled", "disabled"]
    assert [tool["spec"]["name"] for tool in assembly.tools] == ["enabled"]
    assert assembly.configured_count == 2
    assert assembly.configured_schemas_sent == 1


def test_empty_enabled_on_demand_group_is_omitted_then_reappears() -> None:
    tool = {**_tool("remind"), "enabled": False}
    groups = [_group("reminders", ["remind"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    disabled = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert disabled.tools == []
    assert disabled.available_on_demand_groups == 0
    assert (
        load_function_groups(session, ["reminders"], groups, [tool])["status"]
        == "error"
    )

    tool["enabled"] = True
    enabled = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert [item["spec"]["name"] for item in enabled.tools] == ["load_function_groups"]
    assert enabled.available_on_demand_groups == 1


def test_loaded_group_state_is_discarded_when_its_last_member_becomes_unavailable() -> (
    None
):
    tool = _tool("remind")
    groups = [_group("reminders", ["remind"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    load_function_groups(session, ["reminders"], groups, [tool])
    assert session.loaded_group_ids == {"reminders"}

    tool["enabled"] = False
    assert assemble_function_tools([tool], groups, session.loaded_group_ids).tools == []
    assert session.loaded_group_ids == set()

    tool["enabled"] = True
    available_again = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert [item["spec"]["name"] for item in available_again.tools] == [
        "load_function_groups"
    ]
    assert load_function_groups(session, ["reminders"], groups, [tool])["loaded"] == [
        "reminders"
    ]
    restored = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert [item["spec"]["name"] for item in restored.tools] == ["remind"]


def test_runtime_support_controls_group_and_loader_availability() -> None:
    tool = _tool("remind")
    groups = [_group("reminders", ["remind"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)

    no_tools = assemble_function_tools(
        [tool],
        groups,
        session.loaded_group_ids,
        function_tools_supported=False,
    )
    assert no_tools.tools == []
    assert no_tools.available_on_demand_groups == 0

    no_group_loader = assemble_function_tools(
        [tool],
        groups,
        session.loaded_group_ids,
        group_loader_supported=False,
    )
    assert no_group_loader.tools == []
    assert no_group_loader.available_on_demand_groups == 0
    assert (
        load_function_groups(
            session,
            ["reminders"],
            groups,
            [tool],
            group_loader_supported=False,
        )["status"]
        == "error"
    )

    available = assemble_function_tools([tool], groups, session.loaded_group_ids)
    assert [item["spec"]["name"] for item in available.tools] == [
        "load_function_groups"
    ]


def test_runtime_tool_predicate_invalidates_loaded_group_state() -> None:
    tool = _tool("remind")
    groups = [_group("reminders", ["remind"])]
    session = FunctionGroupRuntime().begin("conversation:one", 30)
    load_function_groups(session, ["reminders"], groups, [tool])
    assert session.loaded_group_ids == {"reminders"}

    blocked = assemble_function_tools(
        [tool],
        groups,
        session.loaded_group_ids,
        tool_available=lambda _tool: False,
    )
    assert blocked.tools == []
    assert session.loaded_group_ids == set()


async def test_disabled_tool_is_rejected_at_execution_time(monkeypatch) -> None:
    stale_tool = _tool("sensitive")
    disabled_tool = {**_tool("sensitive"), "enabled": False}
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    persisted_subentry = SimpleNamespace(data={})
    entity.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _entry_id: SimpleNamespace(
                subentries={"agent": persisted_subentry}
            )
        )
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_configured_function_tools_from_data",
        lambda _self, _data: [disabled_tool],
    )
    with pytest.raises(HomeAssistantError, match=r"sensitive.*disabled"):
        await entity._execute_function_tool(
            stale_tool,
            SimpleNamespace(tool_name="sensitive", tool_args={}, id="call-1"),
            None,
            [],
        )


def test_disabled_group_is_rejected_when_execution_resolves_latest_state() -> None:
    stale_tool = _tool("sensitive")
    current_tool = _tool("sensitive")
    latest_data = {
        "function_groups": [_group("protected", ["sensitive"], "always", enabled=False)]
    }
    persisted_subentry = SimpleNamespace(data=latest_data)
    agent = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data={}),
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_entry=lambda _entry_id: SimpleNamespace(
                    subentries={"agent": persisted_subentry}
                )
            )
        ),
        _configured_function_tools_from_data=lambda _data: [current_tool],
    )
    with pytest.raises(HomeAssistantError, match=r"protected.*disabled"):
        latest_function_tool_for_execution(agent, stale_tool)


def test_runtime_diagnostics_name_configured_schema_measurements() -> None:
    runtime = FunctionGroupRuntime()
    assembly = assemble_function_tools([_tool("one")], [], set())
    runtime.record_request(assembly)
    assert runtime.stats() == {
        "active_function_group_sessions": 0,
        "configured_function_tools": 1,
        "configured_function_schemas_sent": 1,
        "available_on_demand_groups": 0,
        "loaded_function_groups": [],
        "serialized_configured_function_schema_characters": (
            assembly.serialized_configured_schema_characters
        ),
    }


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
    expired.last_active = time.monotonic() - (31 * 60)
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


def test_agent_unload_removes_only_its_function_group_runtime(hass) -> None:
    first = reset_function_group_runtime(hass, "entry", "agent-one")
    second = reset_function_group_runtime(hass, "entry", "agent-two")

    remove_function_group_runtime(hass, "entry", "agent-one")

    assert get_function_group_runtime(hass, "entry", "agent-one") is None
    assert get_function_group_runtime(hass, "entry", "agent-two") is second
    assert first is not second


def test_request_and_caller_tool_predicates_are_both_enforced() -> None:
    tools = [
        _tool("available"),
        _tool("blocked_by_caller"),
        _tool("blocked_by_request"),
    ]

    with function_tool_runtime_scope(
        tool_available=lambda tool: tool["spec"]["name"] != "blocked_by_request"
    ):
        assembly = assemble_function_tools(
            tools,
            [],
            set(),
            tool_available=lambda tool: tool["spec"]["name"] != "blocked_by_caller",
        )

    assert [tool["spec"]["name"] for tool in assembly.tools] == ["available"]


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
    assert (
        hass.config_entries.async_update_entry.call_args.kwargs["version"]
        == CONFIG_ENTRY_VERSION
    )


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


def test_group_validation_rejects_non_boolean_enabled_state() -> None:
    group = _group("one", ["one"])
    group["enabled"] = "false"
    with pytest.raises(AgentConfigError, match=r"enabled.*boolean"):
        validate_function_groups([group], [_tool("one")])


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
