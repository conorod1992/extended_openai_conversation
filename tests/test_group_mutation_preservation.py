"""Raw Group identities and hidden members survive ordinary management mutations."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError
from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_quarantine_public_contracts import grouped_agent
from tests.test_group_repair_command_contracts import message, mixed


@pytest.mark.parametrize("operation", ["move", "rename-tool", "delete-tool"])
async def test_incidental_group_updates_preserve_unresolved_members(
    grouped_agent, monkeypatch, operation
):
    hass, entry, subentry, _ = grouped_agent
    original_group = subentry.data["function_groups"][0]
    hidden_members = [
        member for member in original_group["functions"] if not isinstance(member, str)
    ]
    alpha = {
        **original_group,
        "id": "alpha",
        "name": "Alpha",
        "functions": ["native_probe", "missing_plugin_tool", *hidden_members],
    }
    if "name" not in original_group:
        del alpha["name"]
    beta = {**alpha, "id": "beta", "name": "Beta", "functions": []}
    subentry.data = {**subentry.data, "function_groups": [alpha, beta]}
    rules = SimpleNamespace(
        revision=lambda: "rules",
        async_rename_function_reference=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    monkeypatch.setattr(
        ui,
        "_function_reference_state",
        AsyncMock(return_value=(rules, {"request_rules": [], "guest_mode": False})),
    )
    if operation == "move":
        command = message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id="beta",
            group={**beta, "functions": ["native_probe"]},
        )
    elif operation == "rename-tool":
        tool = yaml.safe_load(subentry.data["functions"])[0]
        tool["spec"]["name"] = "renamed_probe"
        command = message(
            entry, subentry, "tools", "save", original_name="native_probe", tool=tool
        )
    else:
        command = message(
            entry, subentry, "tools", "delete", name="native_probe", confirm=True
        )
    await ui.async_management_command(hass, "admin", True, command)
    saved = next(g for g in subentry.data["function_groups"] if g["id"] == "alpha")
    assert saved["functions"] == (
        ["renamed_probe", "missing_plugin_tool", *hidden_members]
        if operation == "rename-tool"
        else ["missing_plugin_tool", *hidden_members]
    )
    if operation == "move":
        assert next(g for g in subentry.data["function_groups"] if g["id"] == "beta")[
            "functions"
        ] == ["native_probe"]


async def test_group_rename_reserves_quarantined_destination_and_references(
    grouped_agent, monkeypatch
):
    hass, entry, subentry, _ = grouped_agent
    beta = {**subentry.data["function_groups"][0], "id": "beta"}
    beta.pop("name", None)
    alpha = {**beta, "id": "alpha", "name": "Alpha", "functions": []}
    subentry.data = {
        **subentry.data,
        "function_groups": [alpha, beta],
        "guest_allowed_group_ids": ["alpha"],
    }
    before = deepcopy(subentry.data)
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    with pytest.raises(HomeAssistantError, match="already exists"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            message(
                entry,
                subentry,
                "tools",
                "save_group",
                original_id="alpha",
                group={**alpha, "id": "beta"},
            ),
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("damage", ["group", "mixed"])
async def test_validate_current_reports_saved_damage(grouped_agent, damage):
    hass, entry, subentry, _ = grouped_agent
    if damage == "mixed":
        mixed(subentry)
    before = deepcopy(subentry.data)
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "tools", "validate_current")
    )
    assert result["valid"] is False
    assert "function_groups" in result["errors"]
    assert result["function_repair"]["group_issues"]
    if damage == "mixed":
        assert "functions" in result["errors"]
    assert subentry.data == before


@pytest.mark.parametrize("action", ["save_group", "delete_group"])
async def test_duplicate_group_identity_cannot_consume_multiple_raw_definitions(
    grouped_agent, monkeypatch, action
):
    hass, entry, subentry, _ = grouped_agent
    raw = subentry.data["function_groups"][0]
    healthy = {**raw, "name": "Healthy", "functions": []}
    subentry.data = {**subentry.data, "function_groups": [healthy, raw]}
    original = deepcopy(subentry.data)
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    command = message(
        entry,
        subentry,
        "tools",
        action,
        original_id=raw["id"],
        group=healthy,
        group_id=raw["id"],
        confirm=True,
    )
    with pytest.raises(HomeAssistantError, match="ambiguous"):
        await ui.async_management_command(hass, "admin", True, command)
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_unrelated_tool_toggle_preserves_duplicate_raw_groups(
    grouped_agent, monkeypatch
):
    hass, entry, subentry, _ = grouped_agent
    raw = deepcopy(subentry.data["function_groups"][0])
    healthy = {**raw, "name": "Healthy", "functions": []}
    groups = [healthy, raw]
    subentry.data = {**subentry.data, "function_groups": groups}
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry, subentry, "tools", "set_enabled", name="native_probe", enabled=True
        ),
    )
    assert subentry.data["function_groups"] == groups


async def test_valid_saved_definitions_pass_current_check(grouped_agent):
    hass, entry, subentry, _ = grouped_agent
    group = {
        **subentry.data["function_groups"][0],
        "name": "Healthy",
        "functions": ["native_probe"],
    }
    subentry.data = {**subentry.data, "function_groups": [group]}
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "tools", "validate_current")
    )
    assert result == {"valid": True, "errors": {}}


async def test_group_only_read_identifies_tools_as_valid(grouped_agent):
    hass, entry, subentry, _ = grouped_agent
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert result["function_repair"]["tools_valid"] is True
    assert result["function_repair"]["invalid_count"] == 0
    assert result["function_repair"]["group_issues"]


@pytest.mark.parametrize("source_id", ["invalid group/id", ""])
async def test_unique_malformed_string_group_id_can_be_explicitly_repaired(
    grouped_agent, monkeypatch, source_id
):
    hass, entry, subentry, _ = grouped_agent
    raw = {**subentry.data["function_groups"][0], "id": source_id}
    subentry.data = {**subentry.data, "function_groups": [raw]}
    candidate = {**raw, "id": "repaired-group", "name": "Repaired", "functions": []}
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id=source_id,
            group=candidate,
        ),
    )
    assert [group["id"] for group in subentry.data["function_groups"]] == [
        "repaired-group"
    ]
    assert result["function_groups"][0]["name"] == "Repaired"


@pytest.mark.parametrize("operation", ["metadata", "move-target", "repair"])
async def test_only_explicit_group_repair_removes_hidden_members(
    grouped_agent, monkeypatch, operation
):
    hass, entry, subentry, _ = grouped_agent
    raw = subentry.data["function_groups"][0]
    alpha = {
        **raw,
        "id": "alpha",
        "name": "Alpha",
        "functions": ["native_probe", "missing_alpha"],
    }
    beta = {**alpha, "id": "beta", "name": "Beta", "functions": ["missing_beta"]}
    subentry.data = {**subentry.data, "function_groups": [alpha, beta]}
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    target = beta if operation == "move-target" else alpha
    candidate = {**target, "functions": ["native_probe"], "enabled": False}
    command = message(
        entry,
        subentry,
        "tools",
        "save_group",
        original_id=target["id"],
        group=candidate,
    )
    if operation == "repair":
        command["repair"] = True
    await ui.async_management_command(hass, "admin", True, command)
    saved = {group["id"]: group for group in subentry.data["function_groups"]}
    assert saved[target["id"]]["enabled"] is False
    assert saved[target["id"]]["functions"] == (
        ["native_probe"]
        if operation == "repair"
        else (
            ["missing_beta", "native_probe"]
            if operation == "move-target"
            else ["native_probe", "missing_alpha"]
        )
    )
    other = "alpha" if operation == "move-target" else "beta"
    assert ("missing_alpha" if other == "alpha" else "missing_beta") in saved[other][
        "functions"
    ]
