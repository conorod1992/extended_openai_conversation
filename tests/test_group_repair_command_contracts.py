"""Public repair commands must tolerate independent damaged Group definitions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError
from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    management_function_quarantine as quarantine,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    validate_function_groups,
)
from custom_components.extended_openai_conversation_responses.agent_test import (
    AgentTestResult,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    repair_revision,
)
from tests.test_group_quarantine_public_contracts import grouped_agent


def message(entry, subentry, section, action, **kwargs):
    return dict(
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section=section,
        action=action,
        revision=repair_revision(subentry),
        **kwargs,
    )


def mixed(subentry):
    tools = yaml.safe_load(subentry.data["functions"])
    broken = deepcopy(tools[0])
    broken["spec"]["name"] = "legacy_probe"
    broken["function"]["name"] = "missing_legacy_implementation"
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([*tools, broken])}
    return tools[0], broken


@pytest.mark.parametrize("operation", ["replace", "delete", "create"])
@pytest.mark.parametrize("damage", ["group", "mixed"])
async def test_group_editor_repairs_or_preserves_raw_siblings(
    grouped_agent, monkeypatch, operation, damage
):
    hass, entry, subentry, original = grouped_agent
    if damage == "mixed":
        mixed(subentry)
    original = deepcopy(subentry.data)
    # Another malformed sibling must survive every targeted mutation.
    sibling = {"id": "other-broken", "functions": [], "opaque": {"keep": True}}
    healthy = {
        **original["function_groups"][0],
        "id": "healthy",
        "name": "Healthy",
        "functions": [],
    }
    subentry.data = {
        **subentry.data,
        "function_groups": [*subentry.data["function_groups"], sibling, healthy],
    }
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    candidate = {**original["function_groups"][0], "name": "Repaired", "functions": []}
    if operation == "create":
        candidate["id"] = "new-group"
    action = "delete_group" if operation == "delete" else "save_group"
    args = (
        dict(group_id="probe-group", confirm=True)
        if operation == "delete"
        else dict(
            group=candidate,
            original_id="probe-group" if operation == "replace" else None,
        )
    )
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "tools", action, **args)
    )
    assert result["revision"] == repair_revision(subentry)
    assert subentry.data["functions"] == original["functions"]
    assert sibling in subentry.data["function_groups"]
    assert healthy in subentry.data["function_groups"]
    if operation == "create":
        assert original["function_groups"][0] in subentry.data["function_groups"]
    elif operation == "delete":
        assert all(
            g.get("id") != "probe-group" for g in subentry.data["function_groups"]
        )
    else:
        assert (
            next(
                g
                for g in subentry.data["function_groups"]
                if g.get("id") == "probe-group"
            )["name"]
            == "Repaired"
        )
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert read["function_repair"]["group_issues"]


@pytest.mark.parametrize(
    "action", ["configuration_get", "save_one", "delete_one", "save"]
)
async def test_mixed_tool_repair_returns_successful_refresh(
    grouped_agent, monkeypatch, action
):
    hass, entry, subentry, _ = grouped_agent
    valid, invalid = mixed(subentry)
    original_groups = deepcopy(subentry.data["function_groups"])
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    monkeypatch.setattr(
        ui,
        "_function_reference_state",
        AsyncMock(return_value=(None, {"request_rules": [], "guest_mode": False})),
    )
    candidate = deepcopy(invalid)
    candidate["function"] = valid["function"]
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry,
            subentry,
            "function_repair",
            action,
            index=1,
            tool=candidate,
            tools=[valid, candidate],
        ),
    )
    assert result["function_repair"]["group_issues"]
    assert subentry.data["function_groups"] == original_groups
    if action != "configuration_get":
        assert result["function_repair"]["invalid_count"] == 0
        assert len(yaml.safe_load(subentry.data["functions"])) == (
            1 if action == "delete_one" else 2
        )
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert read["function_repair"]["group_issues"]


async def test_group_only_provider_diagnostics_are_degraded(grouped_agent, monkeypatch):
    hass, entry, subentry, original = grouped_agent

    async def diagnose(_hass, _entry, safe):
        validate_function_groups(
            safe.data["function_groups"], yaml.safe_load(safe.data["functions"])
        )
        return AgentTestResult("Passed", [])

    monkeypatch.setattr(quarantine, "async_test_configured_agent", diagnose)
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "diagnostics", "test_agent")
    )
    assert result["status"] == "Warning"
    assert any(check["name"] == "Function Groups" for check in result["checks"])
    assert subentry.data == original


async def test_group_only_guest_details_reports_degradation(grouped_agent, monkeypatch):
    hass, entry, subentry, original = grouped_agent
    monkeypatch.setattr(
        ui, "async_get_guest_mode", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(ui, "get_exposed_entities", lambda _: [])
    monkeypatch.setattr(
        ui,
        "resolve_guest_policy",
        lambda *a, **kw: SimpleNamespace(as_diagnostics=lambda: {}),
    )
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(async_list=AsyncMock(return_value=[]))),
    )
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "guest_mode", "details")
    )
    assert result["function_repair"]["group_issues"]
    assert subentry.data == original


@pytest.mark.parametrize("guard", ["invalid", "unknown", "stale", "unconfirmed"])
async def test_group_mutations_keep_validation_and_revision_guards(
    grouped_agent, guard
):
    hass, entry, subentry, original = grouped_agent
    candidate = {**original["function_groups"][0], "name": "Repaired", "functions": []}
    if guard == "invalid":
        candidate.pop("name")
    command = message(
        entry,
        subentry,
        "tools",
        "save_group",
        original_id="probe-group",
        group=candidate,
    )
    if guard == "unknown":
        command["original_id"] = "gone"
    elif guard == "stale":
        command["revision"] = "stale"
        candidate["name"] = "Valid replacement"
    elif guard == "unconfirmed":
        command.update(action="delete_group", group_id="probe-group")
    with pytest.raises(HomeAssistantError):
        await ui.async_management_command(hass, "admin", True, command)
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize(
    "members",
    [["missing"], ["native_probe", "native_probe"], [{"invalid": True}], [["nested"]]],
)
async def test_group_replacements_validate_proposed_members_strictly(
    grouped_agent, members
):
    hass, entry, subentry, original = grouped_agent
    candidate = {
        **original["function_groups"][0],
        "name": "Repaired",
        "functions": members,
    }
    with pytest.raises(HomeAssistantError):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            message(
                entry,
                subentry,
                "tools",
                "save_group",
                original_id="probe-group",
                group=candidate,
            ),
        )
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_tool_edit_retains_noncollection_group_damage(grouped_agent, monkeypatch):
    hass, entry, subentry, _ = grouped_agent
    raw_groups = {"opaque": ["damaged", None]}
    subentry.data = {**subentry.data, "function_groups": raw_groups}
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    monkeypatch.setattr(
        ui,
        "_function_reference_state",
        AsyncMock(return_value=(None, {"request_rules": [], "guest_mode": False})),
    )
    await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry, subentry, "tools", "set_enabled", name="native_probe", enabled=False
        ),
    )
    assert yaml.safe_load(subentry.data["functions"])[0]["enabled"] is False
    assert subentry.data["function_groups"] == raw_groups


async def test_group_creation_cannot_overwrite_quarantined_identifier(grouped_agent):
    hass, entry, subentry, original = grouped_agent
    candidate = {**original["function_groups"][0], "name": "Repaired", "functions": []}
    with pytest.raises(HomeAssistantError, match="already exists"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            message(entry, subentry, "tools", "save_group", group=candidate),
        )
    assert subentry.data == original
