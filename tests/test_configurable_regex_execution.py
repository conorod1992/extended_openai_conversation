"""Regression tests for administrator-configured regex event-loop isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.regex_execution import (
    async_run_configurable_regex,
    install_configurable_regex_isolation,
)


async def _assert_event_loop_progresses_while(awaitable_factory) -> None:
    """Prove deliberately blocking synchronous work does not occupy the event loop."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_work() -> str:
        started.set()
        release.wait(0.2)
        finished.set()
        return "done"

    task = asyncio.create_task(awaitable_factory(blocking_work))
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.001)

    assert started.is_set(), "blocking work never started"
    await asyncio.sleep(0)
    assert not finished.is_set(), "blocking work ran on the event loop"
    assert not task.done(), "blocking work completed before the event loop could advance"

    release.set()
    assert await task == "done"


async def test_configurable_regex_executor_keeps_event_loop_responsive(hass) -> None:
    """The shared regex seam must run synchronous matching outside the HA loop."""

    async def run(blocking_work):
        return await async_run_configurable_regex(hass, blocking_work)

    await _assert_event_loop_progresses_while(run)


async def test_bash_guard_keeps_event_loop_responsive(
    hass, monkeypatch, tmp_path: Path
) -> None:
    """Bash allow-pattern and defensive regex checks use the same executor seam."""
    function = BashFunction()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_guard(*_args, **_kwargs) -> None:
        started.set()
        release.wait(0.2)
        finished.set()

    monkeypatch.setattr(function, "_guard_command", blocking_guard)
    task = asyncio.create_task(
        function._async_guard_command(
            hass,
            "echo ready",
            tmp_path,
            False,
            [r"^echo"],
        )
    )
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.001)

    assert started.is_set(), "Bash guard never started"
    await asyncio.sleep(0)
    assert not finished.is_set(), "Bash guard ran on the event loop"
    assert not task.done(), "Bash guard completed before the event loop could advance"

    release.set()
    await task


def test_runtime_regex_paths_are_installed() -> None:
    """Speech and Function Tool runtime seams are both patched idempotently."""
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
    assert getattr(
        ExtendedOpenAIBaseLLMEntity._execute_function_tool,
        "_extended_openai_configurable_regex_executor",
        False,
    )

    speech_handler = ExtendedOpenAIAgentEntity._async_handle_message
    tool_handler = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    install_configurable_regex_isolation()
    assert ExtendedOpenAIAgentEntity._async_handle_message is speech_handler
    assert ExtendedOpenAIBaseLLMEntity._execute_function_tool is tool_handler
