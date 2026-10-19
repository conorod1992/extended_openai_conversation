"""Failure and concurrency boundaries must preserve authoritative repair data."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message, mixed


@pytest.mark.parametrize(
    "damage",
    ["healthy", "duplicate-assignment", "unresolved-member", "malformed-group"],
)
@pytest.mark.parametrize("mixed_tools", [False, True])
@pytest.mark.parametrize("concurrent_edit", [False, True])
async def test_tool_rename_failure_preserves_complete_raw_configuration(
    duplicate_assignment_agent, monkeypatch, damage, mixed_tools, concurrent_edit
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, gamma = deepcopy(subentry.data["function_groups"])
    if damage == "unresolved-member":
        alpha["functions"].append("missing_plugin_tool")
        beta["functions"] = []
    elif damage == "malformed-group":
        beta.pop("name")
    elif damage == "healthy":
        beta["functions"] = []
    subentry.data = {
        **subentry.data,
        "function_groups": [alpha, beta, gamma],
        "guest_allowed_function_names": ["native_probe"],
        "opaque_extension": {"must_survive": [False, None]},
    }
    if mixed_tools:
        mixed(subentry)
    subentry.data = {
        **subentry.data,
        "functions": "# Retain raw YAML formatting\n" + subentry.data["functions"],
    }
    original = deepcopy(subentry.data)
    latest = None

    async def fail_rule_rename(*_args, **_kwargs):
        nonlocal latest
        assert "renamed_probe" in subentry.data["function_groups"][0]["functions"]
        if concurrent_edit:
            subentry.data = {**subentry.data, "prompt": "Newer edit during rule rename"}
            latest = deepcopy(subentry.data)
        raise RuntimeError("Rule rename failed")

    rules = SimpleNamespace(
        revision=lambda: "rules",
        async_rename_function_reference=AsyncMock(side_effect=fail_rule_rename),
    )
    monkeypatch.setattr(
        ui,
        "_function_reference_state",
        AsyncMock(return_value=(rules, {"request_rules": [], "guest_mode": True})),
    )
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["name"] = "renamed_probe"
    error_type = HomeAssistantError if concurrent_edit else RuntimeError
    error_text = (
        "newer configuration was preserved" if concurrent_edit else "Rule rename failed"
    )
    with pytest.raises(error_type, match=error_text):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            message(
                entry,
                subentry,
                "tools",
                "save",
                original_name="native_probe",
                tool=tool,
            ),
        )
    assert subentry.data == (latest if concurrent_edit else original)
    assert hass.config_entries.async_update_subentry.call_count == (
        1 if concurrent_edit else 2
    )
