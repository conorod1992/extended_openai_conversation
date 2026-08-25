"""Tests for administrator Function Tool state actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_DISABLE_FUNCTION_TOOLS,
    SERVICE_ENABLE_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.services import (
    async_setup_services,
)
from homeassistant.exceptions import HomeAssistantError


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_energy"},
    }


async def test_enable_disable_actions_update_one_or_multiple_tools(
    hass, monkeypatch
) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={"functions": yaml.safe_dump([_tool("one"), _tool("two")])},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        subentries={"agent-1": subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _entity_id: None),
    )

    def apply_update(_entry, _subentry, *, data):
        subentry.data = data

    hass.config_entries.async_update_subentry.side_effect = apply_update
    await async_setup_services(hass, {})
    handlers = {
        call.args[1]: call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[0] == DOMAIN
    }

    await handlers[SERVICE_DISABLE_FUNCTION_TOOLS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "functions": ["one", "two"],
            }
        )
    )
    disabled = yaml.safe_load(subentry.data["functions"])
    assert [tool["enabled"] for tool in disabled] == [False, False]

    await handlers[SERVICE_ENABLE_FUNCTION_TOOLS](
        SimpleNamespace(
            data={
                "config_entry": "entry-1",
                "agent_id": "agent-1",
                "functions": ["two"],
            }
        )
    )
    enabled = yaml.safe_load(subentry.data["functions"])
    assert [tool["enabled"] for tool in enabled] == [False, True]

    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=False)
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await handlers[SERVICE_ENABLE_FUNCTION_TOOLS](
            SimpleNamespace(
                context=SimpleNamespace(user_id="non-admin"),
                data={
                    "config_entry": "entry-1",
                    "agent_id": "agent-1",
                    "functions": ["one"],
                },
            )
        )


async def test_call_function_action_uses_request_scoped_bridge(
    hass, monkeypatch
) -> None:
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_call_active_function",
        execute,
    )
    await async_setup_services(hass, {})
    handler = next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[:2] == (DOMAIN, SERVICE_CALL_FUNCTION)
    )
    result = await handler(
        SimpleNamespace(data={"function": "remember", "arguments": {"fact": "Tuesday"}})
    )
    assert result == {"result": {"ok": True}}
    execute.assert_awaited_once_with("remember", {"fact": "Tuesday"})
