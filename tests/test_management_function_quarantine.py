"""Regression coverage for management behavior while Function Tools need repair."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    function_configuration_mutations as mutations,
    management_function_quarantine as quarantine,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.agent_test import (
    AgentTestResult,
    TestCheck as AgentTestCheck,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)


def _mixed_unknown_native_data() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any]
]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    valid_tool = deepcopy(tools[0])
    invalid_tool = deepcopy(valid_tool)
    invalid_tool["spec"]["name"] = "schedule_deferred_action"
    invalid_tool["function"] = {
        "type": "native",
        "name": "deferred_actions.create_safe",
    }
    mixed = [valid_tool, invalid_tool]
    return (
        {
            **defaults,
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                mixed, sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
        valid_tool,
        invalid_tool,
    )


def test_safe_function_configuration_quarantines_unknown_native() -> None:
    """Management-only normalization keeps valid siblings and removes bad natives."""
    data, valid_tool, invalid_tool = _mixed_unknown_native_data()

    safe = quarantine._safe_function_configuration(data)
    parsed = yaml.safe_load(safe[CONF_FUNCTION_TOOLS])

    assert [tool["spec"]["name"] for tool in parsed] == [valid_tool["spec"]["name"]]
    assert invalid_tool["spec"]["name"] not in {
        name
        for group in safe[CONF_FUNCTION_GROUPS]
        for name in group.get("functions", [])
    }


@pytest.mark.parametrize("updates", [
    {"knowledge_enabled": True}, {"archive_enabled": True},
    {"prompt": "Edited"}, {"chat_model": "gpt-4.1"},
    {"skills": []}, {"temporary_memory": "off"},
])
@pytest.mark.parametrize("broken_groups", [False, True])
def test_field_only_updates_preserve_quarantine_without_section_scope(hass, updates, broken_groups):
    data, valid, invalid = _mixed_unknown_native_data()
    if broken_groups:
        data[CONF_FUNCTION_GROUPS] = [{"id": "broken", "functions": ["missing"]}]
    data.pop("reasoning_effort", None)
    original = deepcopy(data)
    subentry = SimpleNamespace(data=data, subentry_id="quarantined-agent", title="Agent")
    persisted, display = quarantine.merge_unchanged_function_configuration(subentry, updates)
    assert persisted == {**original, **updates}
    assert data == original
    assert display[CONF_FUNCTION_TOOLS] == [valid]
    with pytest.raises(quarantine.HomeAssistantError, match="cannot change Functions"):
        quarantine.merge_unchanged_function_configuration(subentry, {CONF_FUNCTION_TOOLS: [invalid]})


def test_management_merge_preserves_repair_owned_function_fields(
    hass,
) -> None:
    """Saving Guest Mode cannot silently rewrite quarantined Function Tools."""
    data, _valid_tool, _invalid_tool = _mixed_unknown_native_data()
    original_tools = data[CONF_FUNCTION_TOOLS]
    original_groups = deepcopy(data[CONF_FUNCTION_GROUPS])

    subentry = SimpleNamespace(data=data, title="Assistant")
    merged, display = quarantine.merge_unchanged_function_configuration(
        subentry, {"guest_mode_enabled": True}
    )

    assert merged["guest_mode_enabled"] is True
    assert merged[CONF_FUNCTION_TOOLS] == original_tools
    assert merged[CONF_FUNCTION_GROUPS] == original_groups
    assert display[CONF_FUNCTION_TOOLS] == [_valid_tool]


@pytest.mark.parametrize("inspection_error", [False, True])
def test_tolerant_inspection_does_not_change_strict_loading_after_errors(
    hass, monkeypatch, inspection_error
):
    data, valid, _invalid = _mixed_unknown_native_data()
    original = deepcopy(data)
    if inspection_error:
        def fail_inspection(_data):
            raise RuntimeError("inspection failed")

        monkeypatch.setattr(quarantine, "function_tools_issue", fail_inspection)
        with pytest.raises(RuntimeError, match="inspection failed"):
            quarantine.inspect_function_tools(data)
    else:
        assert quarantine.inspect_function_tools(data) == [valid]
    with pytest.raises(quarantine.HomeAssistantError):
        configured_function_tools_from_data(data)
    assert data == original


@pytest.mark.asyncio
async def test_diagnostics_quarantines_invalid_tools_and_reports_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad Function Tool no longer prevents provider diagnostics from running."""
    data, valid_tool, invalid_tool = _mixed_unknown_native_data()
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=object(), data={})
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Assistant",
        data=data,
    )
    observed: dict[str, Any] = {}

    async def original(_hass: Any, _entry: Any, safe_subentry: Any) -> AgentTestResult:
        observed["tools"] = yaml.safe_load(safe_subentry.data[CONF_FUNCTION_TOOLS])
        return AgentTestResult(
            "Passed",
            [AgentTestCheck("Provider request", "Passed", "Request succeeded")],
        )

    monkeypatch.setattr(quarantine, "async_test_configured_agent", original)

    result = await quarantine._tolerant_agent_test(SimpleNamespace(), entry, subentry)

    assert [tool["spec"]["name"] for tool in observed["tools"]] == [
        valid_tool["spec"]["name"]
    ]
    assert invalid_tool["spec"]["name"] not in {
        tool["spec"]["name"] for tool in observed["tools"]
    }
    assert result.status == "Warning"
    assert result.checks[-1].name == "Function Tools"
    assert result.checks[-1].status == "Warning"
    assert "schedule_deferred_action" in result.checks[-1].message


@pytest.mark.asyncio
async def test_overview_fallback_uses_persisted_provider_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated tool damage must not become Unknown provider / No model selected."""

    entry = SimpleNamespace(
        entry_id="entry-1",
        runtime_data=None,
        data={CONF_API_PROVIDER: "openai"},
    )
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={CONF_CHAT_MODEL: "gpt-5.6-luna"},
    )

    from custom_components.extended_openai_conversation_responses import (
        management_setup_health,
    )

    def fail_health(*args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(
        management_setup_health, "build_setup_health_facts", fail_health
    )
    result = management_setup_health.add_setup_health(
        object(), entry, subentry, {"agent": {}}, is_admin=True
    )
    runtime = result["setup_health"]["provider_runtime"]
    assert runtime["provider"] == "openai"
    assert runtime["model"] == "gpt-5.6-luna"
    assert runtime["client_loaded"] is False


def test_management_inspection_clean_paths_use_strict_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {CONF_FUNCTION_TOOLS: "clean"}
    strict_tools = Mock(return_value=[{"spec": {"name": "clean"}}])
    strict_groups = Mock(return_value=[{"id": "g", "functions": ["clean"]}])
    monkeypatch.setattr(quarantine, "function_tools_issue", lambda _raw: ([], None))
    monkeypatch.setattr(quarantine, "_STRICT_CONFIGURED_TOOLS", strict_tools)
    monkeypatch.setattr(quarantine, "_STRICT_VALIDATE_FUNCTION_GROUPS", strict_groups)
    assert quarantine.inspect_function_tools(data) == [{"spec": {"name": "clean"}}]
    strict_tools.assert_called_once_with(data)

    groups = [{"id": "g", "functions": ["clean"]}]
    tools = [{"spec": {"name": "clean"}}]
    assert quarantine.inspect_function_groups(groups, tools) == [
        {"id": "g", "functions": ["clean"]}
    ]
    strict_groups.assert_called_once_with(groups, tools)

def test_management_group_validation_filters_only_quarantined_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, valid, invalid = _mixed_unknown_native_data()
    name = valid["spec"]["name"]
    groups = [{"id": "g", "name": "Group", "description": "Group tools", "loading_mode": "on_demand", "functions": [name, invalid["spec"]["name"], 123]}]
    original = deepcopy(groups)
    result = quarantine.inspect_function_groups(groups, [valid])
    assert result[0]["functions"] == [name]
    assert groups == original


def test_quarantined_preparation_preserves_hidden_members_and_malformed_groups(hass) -> None:
    raw, valid, invalid = _mixed_unknown_native_data()
    name, hidden = valid["spec"]["name"], invalid["spec"]["name"]
    raw[CONF_FUNCTION_GROUPS] = [
        {"id": "g1", "name": "Group", "description": "Tools", "loading_mode": "on_demand", "functions": [name, hidden]},
        {"id": "malformed", "functions": "not-a-list"},
        None,
    ]
    original = deepcopy(raw)
    state = mutations.function_configuration_repair_state(raw)
    assert state is not None
    edited = deepcopy(state.groups)
    edited[0]["description"] = "Updated usable group"

    prepared = mutations.prepare_quarantined_function_configuration(raw, state.valid, edited, state)

    saved_groups = prepared.persisted[CONF_FUNCTION_GROUPS]
    assert saved_groups[0]["functions"] == [name, hidden]
    assert saved_groups[0]["description"] == "Updated usable group"
    assert saved_groups[1:] == original[CONF_FUNCTION_GROUPS][1:]
    assert prepared.persisted[CONF_FUNCTION_TOOLS] == original[CONF_FUNCTION_TOOLS]
    assert raw == original
    assert edited[0]["functions"] == [name]


def test_management_merge_in_quarantine_preserves_raw_function_fields(
    hass,
) -> None:
    raw, valid, _invalid = _mixed_unknown_native_data()
    raw[CONF_FUNCTION_GROUPS] = [{"id": "raw-group"}]
    raw["opaque_extension"] = {"future": [None, False, {"shape": 7}]}
    original = deepcopy(raw)
    merged, display = quarantine.merge_unchanged_function_configuration(
        SimpleNamespace(data=raw, title="Assistant"), {"prompt": "new"}
    )
    assert merged["prompt"] == "new"
    assert merged[CONF_FUNCTION_TOOLS] == original[CONF_FUNCTION_TOOLS]
    assert merged[CONF_FUNCTION_GROUPS] == [{"id": "raw-group"}]
    assert merged["opaque_extension"] == original["opaque_extension"]
    assert display[CONF_FUNCTION_TOOLS] == [valid]
    assert raw == original


def test_tolerant_persist_delegates_when_configuration_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegated = Mock(return_value={"revision": "delegated"})
    monkeypatch.setattr(
        quarantine,
        "function_configuration_repair_state",
        lambda _raw: None,
    )
    monkeypatch.setattr(
        quarantine, "persist_valid_function_configuration", delegated
    )
    hass = SimpleNamespace()
    entry = SimpleNamespace()
    subentry = SimpleNamespace(data={})

    result = quarantine._tolerant_persist_function_configuration(
        hass,
        entry,
        subentry,
        [{"spec": {"name": "ok"}}],
        [],
        extra_updates={"x": 1},
        expected_revision="revision",
    )

    assert result == {"revision": "delegated"}
    delegated.assert_called_once()


def test_tolerant_persist_rejects_stale_duplicate_and_unisolatable_state(monkeypatch, hass) -> None:
    raw, _valid, invalid = _mixed_unknown_native_data()
    monkeypatch.setattr(quarantine, "repair_revision", lambda _subentry: "current")
    subentry = SimpleNamespace(data=raw)
    with pytest.raises(quarantine.HomeAssistantError, match="changed in another tab"):
        quarantine._tolerant_persist_function_configuration(hass, SimpleNamespace(), subentry, [], [], expected_revision="stale")
    with pytest.raises(quarantine.HomeAssistantError, match="already exists"):
        quarantine._tolerant_persist_function_configuration(
            hass, SimpleNamespace(), subentry,
            [{"spec": {"name": invalid["spec"]["name"]}}], [], expected_revision="current"
        )
    subentry.data = {CONF_FUNCTION_TOOLS: "[unclosed"}
    with pytest.raises(quarantine.HomeAssistantError, match="cannot be isolated safely"):
        quarantine._tolerant_persist_function_configuration(hass, SimpleNamespace(), subentry, [], [], expected_revision="current")
    hass.config_entries.async_update_subentry.assert_not_called()


def test_tolerant_persist_keeps_invalid_raw_tool_and_hidden_group_member(monkeypatch, hass) -> None:
    raw, valid, invalid_tool = _mixed_unknown_native_data()
    name, hidden = valid["spec"]["name"], invalid_tool["spec"]["name"]
    raw_groups = [{"id": "g", "functions": [name, hidden]}]
    raw[CONF_FUNCTION_GROUPS] = deepcopy(raw_groups)
    subentry = SimpleNamespace(data=raw)
    monkeypatch.setattr(quarantine, "repair_revision", lambda _subentry: "new-revision")
    hass.config_entries.async_update_subentry.side_effect = lambda _entry, target, *, data: setattr(target, "data", data)
    tools = [deepcopy(valid)]
    tools[0]["spec"]["description"] = "Edited while the other Tool needs repair"
    groups = [{"id": "g", "functions": [name]}]

    result = quarantine._tolerant_persist_function_configuration(
        hass, SimpleNamespace(), subentry, tools, groups,
        extra_updates={"guest_mode_enabled": True}, expected_revision="new-revision"
    )

    assert yaml.safe_load(subentry.data[CONF_FUNCTION_TOOLS]) == [*tools, invalid_tool]
    assert subentry.data[CONF_FUNCTION_GROUPS] == raw_groups
    assert subentry.data["guest_mode_enabled"] is True
    assert {key: result[key] for key in ("functions", "function_groups", "revision")} == {
        "functions": tools,
        # Missing Group metadata remains repair-owned, not a usable response Group.
        "function_groups": [],
        "revision": "new-revision",
    }
    assert result["function_repair"]["persisted_groups"] == raw_groups
    assert result["function_repair"]["group_issues"]


@pytest.mark.asyncio
async def test_tolerant_agent_test_clean_and_collection_level_warning_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_result = AgentTestResult(
        "Passed", [AgentTestCheck("Provider request", "Passed", "ok")]
    )
    provider = AsyncMock(return_value=clean_result)
    monkeypatch.setattr(quarantine, "async_test_configured_agent", provider)
    monkeypatch.setattr(
        quarantine,
        "function_configuration_repair_state",
        lambda _raw: None,
    )
    entry = SimpleNamespace()
    subentry = SimpleNamespace(data={}, subentry_id="agent", title="Assistant")

    assert (
        await quarantine._tolerant_agent_test(
            SimpleNamespace(), entry, subentry
        )
        is clean_result
    )

    monkeypatch.setattr(
        quarantine,
        "function_configuration_repair_state",
        lambda _raw: SimpleNamespace(invalid=[], group_issues=[], field_updates_allowed=False),
    )
    monkeypatch.setattr(
        quarantine,
        "_safe_function_configuration",
        lambda raw: raw,
    )
    warned = AgentTestResult(
        "Passed", [AgentTestCheck("Provider request", "Passed", "ok")]
    )
    provider.return_value = warned

    result = await quarantine._tolerant_agent_test(
        SimpleNamespace(), entry, subentry
    )

    assert result.checks[-1].name == "Function Tools"
    assert result.checks[-1].status == "Warning"
    assert "configuration was quarantined" in result.checks[-1].message
