"""Regression coverage for the Function Tool recovery validation boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

import custom_components.extended_openai_conversation_responses.regex_execution as regex_execution
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionValidationInfrastructureError,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    async_validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)


def _tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": "set_mode",
            "description": "Set a test mode",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "pattern": "^(on|off)$"},
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "service", "service": "test.set_mode"},
    }


def _parallel_tool(name: str) -> dict[str, Any]:
    tool = _tool()
    tool["spec"]["name"] = name
    tool["function"] = {"type": "knowledge", "operation": "search"}
    return tool


def _call(
    mode: str, *, call_id: str = "call-1", tool_name: str = "set_mode"
) -> llm.ToolInput:
    return llm.ToolInput(
        id=call_id,
        tool_name=tool_name,
        tool_args={"mode": mode},
        external=True,
    )


def _chat_log(hass: Any, call: llm.ToolInput) -> conversation.ChatLog:
    return _chat_log_for_calls(hass, [call])


def _chat_log_for_calls(
    hass: Any, calls: list[llm.ToolInput]
) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "validation-boundary")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id="conversation.validation_boundary",
            tool_calls=calls,
        )
    )
    return chat_log


def _entity(hass: Any) -> Any:
    return SimpleNamespace(
        hass=hass,
        entity_id="conversation.validation_boundary",
        _execute_function_tool=AsyncMock(),
    )


def _results(chat_log: conversation.ChatLog) -> list[conversation.ToolResultContent]:
    return [
        item
        for item in chat_log.content
        if isinstance(item, conversation.ToolResultContent)
    ]


async def test_regex_validation_infrastructure_failure_is_not_recoverable(
    hass, monkeypatch
) -> None:
    """Worker/infrastructure failures fail fast and consume no recovery slot."""

    async def unavailable(_hass: Any, _checks: Any) -> list[bool]:
        raise HomeAssistantError("regex worker unavailable")

    monkeypatch.setattr(regex_execution, "async_search_configured_patterns", unavailable)

    tool = _tool()
    call = _call("on")
    chat_log = _chat_log(hass, call)
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(
        FunctionValidationInfrastructureError,
        match="regex worker unavailable",
    ):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=state,
        )

    entity._execute_function_tool.assert_not_awaited()
    assert state.used == 0
    results = _results(chat_log)
    assert len(results) == 1
    assert results[0].tool_result["result"]["status"] == "error"
    assert results[0].tool_result["result"].get("reason") != "correctable_tool_error"


@pytest.mark.parametrize("failure_index", [0, 1, 2])
async def test_parallel_validation_infrastructure_failure_closes_all_calls(
    hass, monkeypatch, failure_index: int
) -> None:
    """A pre-dispatch infrastructure failure closes every retained parallel call."""
    validation_index = 0

    async def fail_selected(_hass: Any, _checks: Any) -> list[bool]:
        nonlocal validation_index
        current = validation_index
        validation_index += 1
        if current == failure_index:
            raise HomeAssistantError(f"regex worker unavailable at {failure_index}")
        return [True]

    monkeypatch.setattr(
        regex_execution, "async_search_configured_patterns", fail_selected
    )

    names = ["first_read", "second_read", "third_read"]
    tools = [_parallel_tool(name) for name in names]
    calls = [
        _call("on", call_id=f"call-{index + 1}", tool_name=name)
        for index, name in enumerate(names)
    ]
    chat_log = _chat_log_for_calls(hass, calls)
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)
    budget = FunctionCallBudget(3)

    with pytest.raises(
        FunctionValidationInfrastructureError,
        match=f"regex worker unavailable at {failure_index}",
    ):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            calls,
            tools,
            budget,
            None,
            [],
            recovery_state=state,
        )

    entity._execute_function_tool.assert_not_awaited()
    assert state.used == 0
    assert budget.used == 3
    results = _results(chat_log)
    assert [result.tool_call_id for result in results] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert len({result.tool_call_id for result in results}) == 3
    assert [result.tool_result["result"]["status"] for result in results] == [
        "error" if index == failure_index else "skipped" for index in range(3)
    ]


async def test_disabled_validation_preserves_original_infrastructure_error(
    hass, monkeypatch
) -> None:
    """The ordinary validator keeps its pre-recovery exception contract."""

    async def unavailable(_hass: Any, _checks: Any) -> list[bool]:
        raise HomeAssistantError("regex worker unavailable")

    monkeypatch.setattr(regex_execution, "async_search_configured_patterns", unavailable)

    with pytest.raises(HomeAssistantError, match="regex worker unavailable") as caught:
        await async_validate_function_arguments(
            hass,
            _tool()["spec"],
            {"mode": "on"},
        )

    assert type(caught.value) is HomeAssistantError


async def test_regex_argument_mismatch_remains_correctable(hass, monkeypatch) -> None:
    """A completed validation that rejects model input still returns correction feedback."""

    async def mismatch(_hass: Any, _checks: Any) -> list[bool]:
        return [False]

    monkeypatch.setattr(regex_execution, "async_search_configured_patterns", mismatch)

    tool = _tool()
    call = _call("maybe")
    chat_log = _chat_log(hass, call)
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [tool],
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_not_awaited()
    assert state.used == 1
    result = _results(chat_log)[0].tool_result["result"]
    assert result["reason"] == "correctable_tool_error"
    assert result["code"] == "invalid_arguments"
    assert "does not match its required pattern" in result["error"]
