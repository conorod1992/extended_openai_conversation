"""Regression coverage for execution-time configured Function Tool checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionNotFound,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
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


def _agent(current_tools: list[dict]):
    latest_data = {"revision": 2}
    latest_subentry = SimpleNamespace(data=latest_data)
    latest_entry = SimpleNamespace(subentries={"agent-1": latest_subentry})
    agent = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=Mock(return_value=latest_entry))
    )
    agent.entry = SimpleNamespace(entry_id="entry-1")
    agent.subentry = SimpleNamespace(subentry_id="agent-1", data={"revision": 1})
    agent._configured_function_tools_from_data = Mock(return_value=current_tools)
    agent._effective_guest_policy = Mock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    return agent


async def test_deleted_tool_fails_closed_before_execution() -> None:
    agent = _agent([])

    with pytest.raises(FunctionNotFound):
        await ExtendedOpenAIAgentEntity._execute_function_tool(
            agent,
            _tool(),
            SimpleNamespace(id="call-1", tool_name="notify", tool_args={}),
            None,
            [],
        )


async def test_disabled_current_tool_fails_closed_before_execution(monkeypatch) -> None:
    current = _tool(enabled=False, service="notify.current")
    agent = _agent([current])
    base_execute = AsyncMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.entity."
        "ExtendedOpenAIBaseLLMEntity._execute_function_tool",
        base_execute,
    )

    with pytest.raises(HomeAssistantError, match="Function Tool `notify` is disabled"):
        await ExtendedOpenAIAgentEntity._execute_function_tool(
            agent,
            _tool(enabled=True),
            SimpleNamespace(id="call-1", tool_name="notify", tool_args={}),
            None,
            [],
        )

    base_execute.assert_not_awaited()
