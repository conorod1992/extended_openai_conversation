"""Security-boundary coverage for Function Tool error recovery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components import conversation
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    guest_mode_denial_result,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)


def _tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": "control_light",
            "description": "Control a light",
            "parameters": {
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "service", "service": "light.turn_on"},
    }


def _call() -> llm.ToolInput:
    return llm.ToolInput(
        id="guest-denied-1",
        tool_name="control_light",
        tool_args={"entity_id": "light.private"},
        external=True,
    )


async def test_guest_mode_denial_is_not_a_recovery_attempt(hass) -> None:
    """A policy denial remains an ordinary security result, never correctable recovery."""
    call = _call()
    denial = conversation.ToolResultContent(
        agent_id="conversation.security",
        tool_call_id=call.id,
        tool_name=call.tool_name,
        tool_result={"result": guest_mode_denial_result()},
    )
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.security",
        _execute_function_tool=AsyncMock(return_value=denial),
    )
    chat_log = conversation.ChatLog(hass, "guest-recovery-boundary")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id=entity.entity_id,
            tool_calls=[call],
        )
    )
    state = ToolRecoveryState(enabled=True)
    budget = FunctionCallBudget(2)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [_tool()],
        budget,
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_awaited_once()
    assert budget.used == 1
    assert state.used == 0
    results = [
        item
        for item in chat_log.content
        if isinstance(item, conversation.ToolResultContent)
    ]
    assert results == [denial]
    assert results[0].tool_result["result"]["reason"] == "guest_mode"
