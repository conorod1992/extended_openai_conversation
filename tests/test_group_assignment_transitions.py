"""Public edits preserve quarantined siblings unless assignment actually changes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_repair_command_contracts import message
from tests.test_save_validation_reuse import _hass_with_agent


@pytest.fixture
def duplicate_assignment_agent(hass, monkeypatch):
    _, entry, subentry = _hass_with_agent()
    alpha = {**subentry.data["function_groups"][0], "id": "alpha", "name": "Alpha"}
    beta = {**deepcopy(alpha), "id": "beta", "name": "Beta"}
    gamma = {**deepcopy(alpha), "id": "gamma", "name": "Gamma", "functions": []}
    subentry.data = {**subentry.data, "function_groups": [alpha, beta, gamma]}
    hass.config_entries.async_get_entry.return_value = entry

    def apply(_entry, _subentry, *, data, title=None):
        subentry.data = data

    hass.config_entries.async_update_subentry.side_effect = apply
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(ui, "_async_validate_configuration_dependencies", AsyncMock())
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _h, _d, result, **kw: result
    )
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    rules = SimpleNamespace(
        revision=lambda: "rules",
        async_rename_function_reference=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        ui,
        "_function_reference_state",
        AsyncMock(return_value=(rules, {"request_rules": [], "guest_mode": False})),
    )
    return hass, entry, subentry


@pytest.mark.parametrize(
    "operation", ["description", "group-toggle", "tool-toggle", "group-id"]
)
async def test_unchanged_assignment_preserves_duplicate_raw_member(
    duplicate_assignment_agent, operation
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, _ = deepcopy(subentry.data["function_groups"])
    if operation == "tool-toggle":
        command = message(
            entry, subentry, "tools", "set_enabled", name="native_probe", enabled=False
        )
    else:
        change = (
            {"description": "Only metadata changed"}
            if operation == "description"
            else {"enabled": False}
            if operation == "group-toggle"
            else {"id": "renamed-alpha"}
        )
        command = message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id="alpha",
            group={**alpha, **change},
        )
    result = await ui.async_management_command(hass, "admin", True, command)
    assert (
        next(g for g in subentry.data["function_groups"] if g["id"] == "beta") == beta
    )
    assert result["function_repair"]["group_issues"]
    assert "beta" not in {g["id"] for g in result["function_groups"]}
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert read["config"]["function_groups"] == result["function_groups"]


@pytest.mark.parametrize("operation", ["move", "rename", "delete"])
async def test_explicit_tool_changes_update_duplicate_assignments(
    duplicate_assignment_agent, operation
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, gamma = deepcopy(subentry.data["function_groups"])
    if operation == "move":
        command = message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id="gamma",
            group={**gamma, "functions": ["native_probe"]},
        )
    elif operation == "rename":
        tool = yaml.safe_load(subentry.data["functions"])[0]
        tool["spec"]["name"] = "renamed_probe"
        command = message(
            entry, subentry, "tools", "save", original_name="native_probe", tool=tool
        )
    else:
        command = message(
            entry, subentry, "tools", "delete", name="native_probe", confirm=True
        )
    result = await ui.async_management_command(hass, "admin", True, command)
    stored = {g["id"]: g for g in subentry.data["function_groups"]}
    assert stored["beta"] == {
        **beta,
        "functions": ["renamed_probe"] if operation == "rename" else [],
    }
    assert stored["alpha"]["functions"] == (
        ["renamed_probe"] if operation == "rename" else []
    )
    assert stored["gamma"]["functions"] == (
        ["native_probe"] if operation == "move" else []
    )
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert result["function_groups"] == read["config"]["function_groups"]


@pytest.mark.parametrize("operation", ["unassign", "delete-group", "repair-group"])
async def test_removing_conflict_preserves_other_groups_assignment(
    duplicate_assignment_agent, operation
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, _ = deepcopy(subentry.data["function_groups"])
    if operation == "delete-group":
        command = message(
            entry, subentry, "tools", "delete_group", group_id="alpha", confirm=True
        )
    else:
        command = message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id="alpha",
            group={**alpha, "functions": []},
            repair=operation == "repair-group",
        )
    result = await ui.async_management_command(hass, "admin", True, command)
    assert (
        next(g for g in subentry.data["function_groups"] if g["id"] == "beta") == beta
    )
    assert "beta" in {g["id"] for g in result["function_groups"]}
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert result["function_groups"] == read["config"]["function_groups"]
