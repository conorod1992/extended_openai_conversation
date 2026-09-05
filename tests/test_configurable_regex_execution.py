"""Regression tests for administrator-configured regex process isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.function_execution import (
    async_validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.regex_execution import (
    async_search_configured_patterns,
    install_configurable_regex_isolation,
)


class _ExecutorHass:
    """Minimal HA-shaped object with genuine executor semantics for focused tests."""

    async def async_add_executor_job(self, target, *args):
        return await asyncio.get_running_loop().run_in_executor(None, target, *args)


async def test_configured_pattern_search_returns_normal_results() -> None:
    hass = _ExecutorHass()

    assert await async_search_configured_patterns(
        hass,
        [(r"^hello", "hello world", 0), (r"^world", "hello world", 0)],
    ) == [True, False]


async def test_pathological_function_pattern_is_bounded_without_stalling_loop() -> None:
    """Catastrophic backtracking is killed outside the HA process at the deadline."""
    hass = _ExecutorHass()
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "pattern": r"^(a+)+$"}
            },
            "required": ["value"],
        }
    }

    task = asyncio.create_task(
        async_validate_function_arguments(
            hass,
            spec,
            {"value": "a" * 30 + "!"},
        )
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.sleep(0.02)
    heartbeat_delay = loop.time() - started

    assert heartbeat_delay < 0.2
    with pytest.raises(HomeAssistantError, match="execution limit"):
        await asyncio.wait_for(task, timeout=2)


async def test_function_pattern_validation_preserves_valid_result() -> None:
    hass = _ExecutorHass()
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string", "pattern": r"^ok$"}},
            "required": ["value"],
        }
    }

    assert await async_validate_function_arguments(hass, spec, {"value": "ok"}) == {
        "value": "ok"
    }


async def test_bash_allow_pattern_is_bounded_without_stalling_loop(tmp_path: Path) -> None:
    """Bash's administrator allowlist uses the same killable regex boundary."""
    hass = _ExecutorHass()
    function = BashFunction()
    task = asyncio.create_task(
        function._async_guard_command(  # type: ignore[arg-type]
            hass,
            "a" * 30 + "!",
            tmp_path,
            False,
            [r"^(a+)+$"],
        )
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.sleep(0.02)
    heartbeat_delay = loop.time() - started

    assert heartbeat_delay < 0.2
    with pytest.raises(HomeAssistantError, match="execution limit"):
        await asyncio.wait_for(task, timeout=2)


def test_runtime_regex_paths_are_installed() -> None:
    """Only completed-response speech still needs a runtime monkey patch."""
    install_configurable_regex_isolation()

    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    assert getattr(
        ExtendedOpenAIAgentEntity._async_handle_message,
        "_extended_openai_configurable_regex_executor",
        False,
    )
    assert not getattr(
        ExtendedOpenAIBaseLLMEntity._execute_function_tool,
        "_extended_openai_configurable_regex_executor",
        False,
    )

    speech_handler = ExtendedOpenAIAgentEntity._async_handle_message
    tool_handler = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    install_configurable_regex_isolation()
    assert ExtendedOpenAIAgentEntity._async_handle_message is speech_handler
    assert ExtendedOpenAIBaseLLMEntity._execute_function_tool is tool_handler
