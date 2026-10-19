"""Unreadable Rule storage cannot authorize rolling back acknowledged Tool data."""

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
async def test_unknown_rule_outcome_retains_last_acknowledged_tool_configuration(
    duplicate_assignment_agent, monkeypatch, route, committed, cancelled
):
    hass, entry, subentry = duplicate_assignment_agent
    monkeypatch.setattr(request_rules, "validate_rule", lambda value, **_kwargs: value)
    store = MemoryStore()
    rules = request_rules.RequestRules(store)
    rules._initialized = True
    rules._rules = [
        yaml.safe_load(
            yaml.safe_dump(_nested_rule()).replace("nested_tool", "native_probe")
        )
    ]
    rules._sort_and_compile()
    await rules._async_save_locked()
    references = {
        "request_rules": rules.function_references("native_probe"),
        "guest_mode": False,
    }
    monkeypatch.setattr(
        ui, "_function_reference_state", AsyncMock(return_value=(rules, references))
    )
    real_save, real_load = store.async_save, store.async_load
    acknowledged = None

    async def fail_save(candidate):
        nonlocal acknowledged
        acknowledged = deepcopy(subentry.data)
        if committed:
            await real_save(candidate)
        if cancelled:
            raise asyncio.CancelledError()
        raise OSError("Rule write acknowledgement failed")

    store.async_save = fail_save
    store.async_load = AsyncMock(side_effect=OSError("Rule storage unreadable"))
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["name"] = "renamed_probe"
    command = (
        message(
            entry, subentry, "tools", "save", original_name="native_probe", tool=tool
        )
        if route == "normal"
        else message(entry, subentry, "function_repair", "save_one", index=0, tool=tool)
    )
    error = asyncio.CancelledError if cancelled else (HomeAssistantError, OSError)
    with pytest.raises(error) as caught:
        await ui.async_management_command(hass, "admin", True, command)
    assert not rules._initialized
    assert rules.function_references("native_probe") == []
    assert rules.function_references("renamed_probe") == []
    assert subentry.data == acknowledged
    assert hass.config_entries.async_update_subentry.call_count == 1
    if not cancelled:
        assert isinstance(caught.value, HomeAssistantError)
        assert "cannot be verified" in str(caught.value)
    # A successful fresh read identifies the actual outcome. Before that read,
    # neither absence of references nor the write exception proves non-commit.
    store.async_load = real_load
    fresh = request_rules.RequestRules(store)
    await fresh.async_initialize()
    assert fresh._initialized
    assert (
        fresh.function_references("renamed_probe" if committed else "native_probe")
        == references["request_rules"]
    )
