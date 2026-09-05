"""Regression tests for configured Function Tool runtime contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses import entity as entity_module
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    split_legacy_execution_delay,
)


class _ExecutorHass:
    """Minimal HA-shaped object with real executor behavior."""

    async def async_add_executor_job(self, target, *args):
        return await asyncio.get_running_loop().run_in_executor(None, target, *args)


def _function_tool(parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "spec": {
            "name": "test_tool",
            "parameters": parameters
            or {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "function": {"type": "template", "template": "unused"},
    }


def _tool_input(arguments: dict[str, Any] | None = None) -> llm.ToolInput:
    return llm.ToolInput(
        id="call-1",
        tool_name="test_tool",
        tool_args=arguments or {},
        external=True,
    )


def _entity() -> SimpleNamespace:
    return SimpleNamespace(
        hass=_ExecutorHass(),
        entity_id="conversation.test",
        entry=SimpleNamespace(
            async_create_task=lambda *_args, **_kwargs: pytest.fail(
                "unexpected background execution"
            )
        ),
        should_run_in_background=ExtendedOpenAIBaseLLMEntity.should_run_in_background,
        get_delayed_function_config=(
            ExtendedOpenAIBaseLLMEntity.get_delayed_function_config
        ),
    )


@pytest.mark.parametrize(
    "return_value",
    [
        {"status": "ok", "items": [1, 2]},
        ["alpha", {"nested": True}],
    ],
)
async def test_structured_function_results_are_preserved(
    monkeypatch, return_value: Any
) -> None:
    """JSON-compatible Function values must not be converted to Python repr strings."""

    class Function:
        async def execute(self, *_args, **_kwargs):
            return return_value

    monkeypatch.setattr(entity_module, "get_function", lambda _type: Function())
    target = _entity()

    result = await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        target,
        _function_tool(),
        _tool_input(),
        None,
        [],
    )

    assert result.tool_result == {"result": return_value}


def test_tool_result_serialization_is_deterministic() -> None:
    """Both provider adapters must emit sorted deterministic JSON for tool results."""
    content = conversation.ToolResultContent(
        agent_id="conversation.test",
        tool_call_id="call-1",
        tool_name="test_tool",
        tool_result={"result": {"z": 2, "a": {"y": 1, "b": 0}}},
    )

    chat = _convert_content_to_param([content])
    responses = _convert_content_to_responses_param([content])

    expected = '{"result":{"a":{"b":0,"y":1},"z":2}}'
    assert chat[0]["content"] == expected
    assert responses[0]["output"] == expected


async def test_expected_execution_failure_becomes_tool_result(monkeypatch) -> None:
    """Expected HA execution errors are returned to the model for recovery."""

    class Function:
        async def execute(self, *_args, **_kwargs):
            raise HomeAssistantError("action is unavailable")

    monkeypatch.setattr(entity_module, "get_function", lambda _type: Function())

    result = await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        _entity(),
        _function_tool(),
        _tool_input(),
        None,
        [],
    )

    assert result.tool_result == {
        "result": {"status": "error", "error": "action is unavailable"}
    }


async def test_cancellation_still_propagates(monkeypatch) -> None:
    """Task cancellation must never be converted into an ordinary tool failure."""

    class Function:
        async def execute(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(entity_module, "get_function", lambda _type: Function())

    with pytest.raises(asyncio.CancelledError):
        await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            _entity(),
            _function_tool(),
            _tool_input(),
            None,
            [],
        )


async def test_programming_error_still_propagates(monkeypatch) -> None:
    """Unexpected programming errors must not masquerade as failed tool results."""

    class Function:
        async def execute(self, *_args, **_kwargs):
            raise TypeError("programming bug")

    monkeypatch.setattr(entity_module, "get_function", lambda _type: Function())

    with pytest.raises(TypeError, match="programming bug"):
        await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            _entity(),
            _function_tool(),
            _tool_input(),
            None,
            [],
        )


async def test_real_delay_argument_reaches_function(monkeypatch) -> None:
    """An ordinary Function argument literally named delay is not scheduling metadata."""
    received: list[dict[str, Any]] = []

    class Function:
        async def execute(
            self,
            _hass,
            _config,
            arguments,
            _llm_context,
            _exposed_entities,
        ):
            received.append(arguments)
            return "ok"

    monkeypatch.setattr(entity_module, "get_function", lambda _type: Function())
    parameters = {
        "type": "object",
        "properties": {"delay": {"type": "number"}},
        "required": ["delay"],
        "additionalProperties": False,
    }

    result = await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        _entity(),
        _function_tool(parameters),
        _tool_input({"delay": 2.5}),
        None,
        [],
    )

    assert received == [{"delay": 2.5}]
    assert result.tool_result == {"result": "ok"}


def test_documented_legacy_delay_is_split_from_arguments() -> None:
    """Existing object-shaped scheduling configurations retain their old meaning."""
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "delay": {
                    "type": "object",
                    "properties": {
                        "hours": {"type": "integer"},
                        "minutes": {"type": "integer"},
                        "seconds": {"type": "integer"},
                    },
                },
                "message": {"type": "string"},
            },
        }
    }

    arguments, delay = split_legacy_execution_delay(
        spec, {"delay": {"seconds": 10}, "message": "hello"}
    )

    assert arguments == {"message": "hello"}
    assert delay == {"seconds": 10}
