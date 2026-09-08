"""Request Rule Function Tool calls honor live configured availability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.exceptions import HomeAssistantError


def _tool(*, enabled: bool = True, service: str = "notify.old") -> dict:
    return {
        "enabled": enabled,
        "spec": {
            "name": "notify",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "service", "service": service},
    }


def _group(*, enabled: bool) -> dict:
    return {
        "id": "notifications",
        "name": "Notifications",
        "description": "Notification tools",
        "loading_mode": "always",
        "functions": ["notify"],
        "enabled": enabled,
    }


def _agent(current_tools: list[dict], groups: list[dict]):
    latest_data = {"function_groups": groups}
    latest_subentry = SimpleNamespace(data=latest_data)
    latest_entry = SimpleNamespace(subentries={"agent-1": latest_subentry})
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=Mock(return_value=latest_entry))
    )
    agent.entry = SimpleNamespace(entry_id="entry-1")
    agent.subentry = SimpleNamespace(subentry_id="agent-1", data={})
    agent._configured_function_tools_from_data = Mock(return_value=current_tools)
    agent._execute_function_tool = AsyncMock(
        return_value=SimpleNamespace(tool_result={"result": ""})
    )
    agent._get_exposed_entities = Mock(return_value=[])
    return agent


async def test_request_rule_rejects_tool_in_disabled_group() -> None:
    agent = _agent([_tool()], [_group(enabled=False)])

    with pytest.raises(HomeAssistantError, match="Function Group `notifications` is disabled"):
        await ExtendedOpenAIAgentEntity._async_execute_request_rule_function(
            agent,
            "notify",
            {},
            None,
        )

    agent._execute_function_tool.assert_not_awaited()


async def test_request_rule_uses_latest_configured_definition() -> None:
    current = _tool(service="notify.current")
    agent = _agent([current], [_group(enabled=True)])

    await ExtendedOpenAIAgentEntity._async_execute_request_rule_function(
        agent,
        "notify",
        {},
        None,
    )

    agent._execute_function_tool.assert_awaited_once()
    assert agent._execute_function_tool.await_args.args[0] == current
