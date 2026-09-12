"""Regression tests for Function Tool configuration persistence."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    DOMAIN,
)


def _native_tool(name: str) -> dict:
    """Return a small, valid configured native Function Tool."""
    return {
        "spec": {
            "name": name,
            "description": f"Test tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "add_automation"},
    }


@pytest.mark.asyncio
async def test_configuration_update_rejects_mixed_invalid_function_tools_atomically(
    hass, monkeypatch
) -> None:
    """Reject one bad tool without partially saving valid siblings, then recover."""
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.entry_id = "entry-1"

    subentry = MagicMock()
    subentry.subentry_id = "agent-1"
    subentry.subentry_type = "conversation"
    subentry.title = "Test agent"
    subentry.data = agent_config_defaults()
    entry.subentries = {subentry.subentry_id: subentry}

    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry

    def persist_subentry(_entry, target, *, data, title):
        target.data = deepcopy(data)
        target.title = title

    hass.config_entries.async_update_subentry.side_effect = persist_subentry
    monkeypatch.setattr(
        management_ui,
        "local_handling_snapshot",
        lambda *_args, **_kwargs: {},
    )

    base_message = {
        "section": "configuration",
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
    }

    initial = await management_ui.async_management_command(
        hass,
        "admin-user",
        True,
        {**base_message, "action": "get"},
    )

    tool_a = _native_tool("tool_a")
    saved_a = await management_ui.async_management_command(
        hass,
        "admin-user",
        True,
        {
            **base_message,
            "action": "update",
            "revision": initial["revision"],
            "config": {CONF_FUNCTION_TOOLS: [tool_a]},
        },
    )
    assert [
        tool["spec"]["name"] for tool in configured_function_tools_from_data(subentry.data)
    ] == ["tool_a"]

    hass.config_entries.async_update_subentry.reset_mock()
    before_failure = deepcopy(subentry.data)
    revision_before_failure = saved_a["revision"]

    malformed_b = _native_tool("tool_b")
    malformed_b["spec"]["parameters"] = "not-an-object"
    tool_c = _native_tool("tool_c")

    with pytest.raises(AgentConfigError) as exc_info:
        await management_ui.async_management_command(
            hass,
            "admin-user",
            True,
            {
                **base_message,
                "action": "update",
                "revision": revision_before_failure,
                "config": {
                    CONF_FUNCTION_TOOLS: [tool_a, malformed_b, tool_c],
                },
            },
        )

    assert exc_info.value.field == f"{CONF_FUNCTION_TOOLS}[1].spec.parameters"
    assert str(exc_info.value).endswith("spec.parameters: must be an object")
    hass.config_entries.async_update_subentry.assert_not_called()
    assert subentry.data == before_failure
    assert [
        tool["spec"]["name"] for tool in configured_function_tools_from_data(subentry.data)
    ] == ["tool_a"]

    after_failure = await management_ui.async_management_command(
        hass,
        "admin-user",
        True,
        {**base_message, "action": "get"},
    )
    assert after_failure["revision"] == revision_before_failure

    corrected_b = _native_tool("tool_b")
    recovered = await management_ui.async_management_command(
        hass,
        "admin-user",
        True,
        {
            **base_message,
            "action": "update",
            "revision": revision_before_failure,
            "config": {
                CONF_FUNCTION_TOOLS: [tool_a, corrected_b, tool_c],
            },
        },
    )

    assert recovered["revision"] != revision_before_failure
    assert [
        tool["spec"]["name"] for tool in configured_function_tools_from_data(subentry.data)
    ] == ["tool_a", "tool_b", "tool_c"]
    hass.config_entries.async_update_subentry.assert_called_once()
