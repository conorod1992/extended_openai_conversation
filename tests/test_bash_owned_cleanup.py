"""Shell cancellation retains cleanup ownership and conservative effect evidence."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as receipts,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.functions import (
    bash,
    workspace,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm


class _Process:
    """Own one controllable subprocess without starting an external command."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.returncode = None
        self.stdout = None
        self.stderr = None

    async def wait(self):
        self.entered.set()
        await self.release.wait()
        return self.returncode

    def finish(self, code=0):
        self.returncode = code
        self.release.set()


def _function(monkeypatch, tmp_path):
    function = bash.BashFunction()
    monkeypatch.setattr(function, "get_working_dir", lambda _hass: Path(tmp_path))
    monkeypatch.setattr(function, "_async_guard_command", AsyncMock())
    monkeypatch.setattr(workspace, "async_ensure_workspace", AsyncMock())
    config = {
        "allow_unsafe_shell": True,
        "command": SimpleNamespace(
            async_render=lambda *_args, **_kwargs: "held command"
        ),
    }
    return function, config


async def _invoke(function, config, entity, call):
    tool = {
        "spec": {
            "name": call.tool_name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "bash", "allow_unsafe_shell": True},
    }
    with receipts.execution_attempt(entity, call, tool):
        return await function.execute(SimpleNamespace(), config, {}, None, [])


@pytest.mark.parametrize("cancels", [1, 3, 5])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_repeated_shell_cancellation_waits_for_owned_cleanup(
    monkeypatch, tmp_path, cancels, cleanup_fails
):
    function, config = _function(monkeypatch, tmp_path)
    process = _Process()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(bash.asyncio, "create_subprocess_shell", spawn)
    entered, release = asyncio.Event(), asyncio.Event()
    cleanups, completed, cleanup_tasks = [], [], []
    failure = HomeAssistantError("controlled cleanup failure")

    async def cleanup(owned_process, stdout, stderr, *, graceful):
        assert owned_process is process
        cleanups.append(graceful)
        cleanup_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
        process.finish(-15)
        await asyncio.gather(stdout, stderr)
        completed.append(True)
        if cleanup_fails:
            raise failure

    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    entity = SimpleNamespace()
    call = llm.ToolInput(id="cancelled-shell", tool_name="owned_shell", tool_args={})

    async def invoke():
        with propagate_function_execution_errors():
            return await _invoke(function, config, entity, call)

    owner = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(process.entered.wait(), 1)
        owner.cancel("original shell cancellation")
        await asyncio.wait_for(entered.wait(), 1)
        for index in range(1, cancels):
            owner.cancel(f"later shell cancellation {index}")
            await asyncio.sleep(0)
            assert not owner.done()
        assert not owner.done() and completed == []
        assert cleanups == [True]
        assert spawn.await_count == 1
        release.set()
        if cleanup_fails:
            with pytest.raises(HomeAssistantError) as caught:
                await owner
            assert caught.value is failure
        else:
            with pytest.raises(asyncio.CancelledError) as caught:
                await owner
            assert caught.value.args == ("original shell cancellation",)
        assert completed == [True]
        assert cleanups == [True]
        assert spawn.await_count == 1
        effects = entity._execution_effect_receipts[id(call)]
        assert len(effects) == 1
        assert effects[0].kind == "bash"
        assert effects[0].outcome == "uncertain"
    finally:
        release.set()
        process.finish(-15)
        # Also observe a shielded task abandoned by the baseline's second cancel.
        await asyncio.gather(owner, *cleanup_tasks, return_exceptions=True)


async def test_cleanup_failure_keeps_legacy_backend_failure_result(
    monkeypatch, tmp_path
):
    function, config = _function(monkeypatch, tmp_path)
    process = _Process()
    monkeypatch.setattr(
        bash.asyncio, "create_subprocess_shell", AsyncMock(return_value=process)
    )

    async def cleanup(*_args, **_kwargs):
        process.finish(-15)
        raise HomeAssistantError("controlled legacy cleanup failure")

    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    entity = SimpleNamespace()
    call = llm.ToolInput(id="failed-cleanup", tool_name="owned_shell", tool_args={})
    owner = asyncio.create_task(_invoke(function, config, entity, call))
    try:
        await asyncio.wait_for(process.entered.wait(), 1)
        owner.cancel("original shell cancellation")
        result = await owner
        assert result["error"] == "controlled legacy cleanup failure"
        assert entity._execution_effect_receipts[id(call)][0].outcome == "uncertain"
    finally:
        process.finish(-15)
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.parametrize("cancels", [0, 1, 3])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_timeout_retains_forced_cleanup_through_cancellation(
    monkeypatch, tmp_path, cancels, cleanup_fails
):
    function, config = _function(monkeypatch, tmp_path)
    process = _Process()

    async def timed_out():
        process.entered.set()
        raise TimeoutError("controlled process timeout")

    monkeypatch.setattr(process, "wait", timed_out)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(bash.asyncio, "create_subprocess_shell", spawn)
    entered, release = asyncio.Event(), asyncio.Event()
    cleanups, completed, cleanup_tasks = [], [], []
    failure = HomeAssistantError("controlled forced cleanup failure")

    async def cleanup(owned_process, stdout, stderr, *, graceful):
        assert owned_process is process
        cleanups.append(graceful)
        cleanup_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
        process.finish(-9)
        await asyncio.gather(stdout, stderr)
        completed.append(True)
        if cleanup_fails:
            raise failure

    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    entity = SimpleNamespace()
    call = llm.ToolInput(id="timed-out-shell", tool_name="owned_shell", tool_args={})

    async def invoke():
        with propagate_function_execution_errors():
            return await _invoke(function, config, entity, call)

    owner = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for index in range(cancels):
            owner.cancel(f"cancel during forced cleanup {index}")
            await asyncio.sleep(0)
            assert not owner.done()
        assert cleanups == [False]
        assert completed == []
        assert spawn.await_count == 1
        release.set()
        if cleanup_fails:
            with pytest.raises(HomeAssistantError) as caught:
                await owner
            assert caught.value is failure
        elif cancels:
            with pytest.raises(asyncio.CancelledError) as caught:
                await owner
            assert caught.value.args == ("cancel during forced cleanup 0",)
        else:
            with pytest.raises(
                HomeAssistantError,
                match=f"Command timed out after {bash.SHELL_TIMEOUT:g} seconds",
            ):
                await owner
        assert completed == [True]
        assert cleanups == [False]
        assert spawn.await_count == 1
        effects = entity._execution_effect_receipts[id(call)]
        assert len(effects) == 1
        assert effects[0].kind == "bash" and effects[0].outcome == "uncertain"
    finally:
        release.set()
        process.finish(-9)
        # A baseline timeout performs cleanup directly inside the owner task.
        await asyncio.gather(owner, *cleanup_tasks, return_exceptions=True)


async def test_successful_shell_has_no_cleanup_and_completes_receipt(
    monkeypatch, tmp_path
):
    function, config = _function(monkeypatch, tmp_path)
    process = _Process()
    process.finish()
    spawn = AsyncMock(return_value=process)
    cleanup = AsyncMock()
    monkeypatch.setattr(bash.asyncio, "create_subprocess_shell", spawn)
    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    entity = SimpleNamespace()
    call = llm.ToolInput(id="successful-shell", tool_name="owned_shell", tool_args={})
    result = await _invoke(function, config, entity, call)
    assert result == {"exit_code": 0, "stdout": ""}
    spawn.assert_awaited_once()
    cleanup.assert_not_awaited()
    assert entity._execution_effect_receipts[id(call)][0].outcome == "completed"


async def test_owned_cleanup_does_not_serialize_an_independent_shell(
    monkeypatch, tmp_path
):
    function, config = _function(monkeypatch, tmp_path)
    held, independent = _Process(), _Process()
    independent.finish()
    spawn = AsyncMock(side_effect=[held, independent])
    monkeypatch.setattr(bash.asyncio, "create_subprocess_shell", spawn)
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup(process, stdout, stderr, *, graceful):
        assert process is held and graceful is True
        entered.set()
        await release.wait()
        held.finish(-15)
        await asyncio.gather(stdout, stderr)

    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    owner = asyncio.create_task(
        function.execute(SimpleNamespace(), config, {}, None, [])
    )
    try:
        await asyncio.wait_for(held.entered.wait(), 1)
        owner.cancel("original shell cancellation")
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(
            function.execute(SimpleNamespace(), config, {}, None, []), 1
        )
        assert result == {"exit_code": 0, "stdout": ""}
        assert not owner.done()
        assert spawn.await_count == 2
        release.set()
        with pytest.raises(asyncio.CancelledError, match="original shell cancellation"):
            await owner
    finally:
        release.set()
        held.finish(-15)
        await asyncio.gather(owner, return_exceptions=True)
