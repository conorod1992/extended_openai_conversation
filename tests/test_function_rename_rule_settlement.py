"""Tool/Group rollback must respect the rule store's actual committed outcome."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses import request_rules
from tests.test_function_dependency_integrity import _nested_rule
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message
from tests.test_request_rules import MemoryStore


@pytest.mark.parametrize("route", ["normal", "repair"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_rename_settles_tools_and_groups_with_durable_rule_state(
    duplicate_assignment_agent, monkeypatch, route, committed, cancelled
):
    hass, entry, subentry = duplicate_assignment_agent
    monkeypatch.setattr(request_rules, "validate_rule", lambda value, **_kwargs: value)
    store = MemoryStore()
    rules = request_rules.RequestRules(store)
    rules._initialized = True
    rule = _nested_rule()
    rule = yaml.safe_load(yaml.safe_dump(rule).replace("nested_tool", "native_probe"))
    rules._rules = [rule]
    rules._sort_and_compile()
    await rules._async_save_locked()
    references = {
        "request_rules": rules.function_references("native_probe"),
        "guest_mode": False,
    }
    monkeypatch.setattr(
        ui, "_function_reference_state", AsyncMock(return_value=(rules, references))
    )
    real_save = store.async_save

    async def fail_save(candidate):
        if committed:
            await real_save(candidate)
        if cancelled:
            raise asyncio.CancelledError()
        raise OSError("Lost rule save acknowledgement")

    store.async_save = fail_save
    before = deepcopy(subentry.data)
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["name"] = "renamed_probe"
    command = (
        message(
            entry, subentry, "tools", "save", tool=tool, original_name="native_probe"
        )
        if route == "normal"
        else message(entry, subentry, "function_repair", "save_one", tool=tool, index=0)
    )
    error = (
        asyncio.CancelledError
        if cancelled
        else HomeAssistantError
        if committed
        else OSError
    )
    pattern = (
        None
        if cancelled
        else "were saved"
        if committed
        else "Lost rule save acknowledgement"
    )
    with pytest.raises(error, match=pattern):
        await ui.async_management_command(hass, "admin", True, command)
    expected_name = "renamed_probe" if committed else "native_probe"
    assert (
        yaml.safe_load(subentry.data["functions"])[0]["spec"]["name"] == expected_name
    )
    assert [g["functions"] for g in subentry.data["function_groups"]] == [
        [expected_name],
        [expected_name],
        [],
    ]
    assert rules.function_references(expected_name) == references["request_rules"]
    fresh_rules = request_rules.RequestRules(store)
    await fresh_rules.async_initialize()
    assert fresh_rules.function_references(expected_name) == references["request_rules"]
    if not committed:
        assert subentry.data == before
