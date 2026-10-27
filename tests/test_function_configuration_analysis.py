"""Read-only repair analysis is independent of management mutation policy."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    function_configuration_repair as analysis,
    function_tool_quarantine as runtime,
    management_function_repair as management,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from homeassistant.exceptions import HomeAssistantError


async def test_backup_and_runtime_isolation_do_not_use_management_analysis(
    hass, monkeypatch
):
    good = {
        "spec": {"name": "usable", "parameters": {"type": "object"}},
        "function": {"type": "template", "value_template": "{{ 42 }}"},
    }
    bad = {
        "spec": {"name": "future", "parameters": {"type": "object"}},
        "function": {"type": "native", "name": "future.unavailable"},
        "future_extension": {"retain": [None, {"version": 7}]},
    }
    source = {
        "functions": [good, bad],
        "function_groups": [],
        "future_agent_field": {"retain": True},
    }
    original = deepcopy(source)
    forbidden = Mock(side_effect=AssertionError("management operation used"))
    monkeypatch.setattr(management, "function_configuration_repair_state", forbidden)
    monkeypatch.setattr(management, "update_live_subentry", forbidden)

    state = analysis.function_configuration_repair_state(source)
    assert state is not None
    assert [tool["spec"]["name"] for tool in state.valid] == ["usable"]
    assert state.invalid[0]["index"] == 1
    assert (
        backup.private_configuration_snapshot(source)["functions"]
        == original["functions"]
    )
    assert backup.recoverable_configuration_snapshot(source)["future_agent_field"] == {
        "retain": True
    }

    # Runtime may execute strictly validated siblings, never the invalid source.
    names_token = runtime._RUNTIME_QUARANTINED_FUNCTION_NAMES.set(frozenset())
    all_token = runtime._RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(False)
    try:
        tools = runtime._runtime_configured_function_tools(source)
        assert [tool["spec"]["name"] for tool in tools] == ["usable"]
        assert runtime._RUNTIME_QUARANTINED_FUNCTION_NAMES.get() == frozenset(
            {"future"}
        )
        with pytest.raises(HomeAssistantError):
            configured_function_tools_from_data(source)
    finally:
        runtime._RUNTIME_QUARANTINE_ALL_FUNCTIONS.reset(all_token)
        runtime._RUNTIME_QUARANTINED_FUNCTION_NAMES.reset(names_token)

    assert source == original
    forbidden.assert_not_called()
