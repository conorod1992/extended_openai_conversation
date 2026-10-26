"""Real asyncio cleanup observation must not log private dependency exceptions."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.functions import bash
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests.test_bash_owned_cleanup import _function, _invoke, _Process


@pytest.mark.parametrize("boundary", ["cancel", "timeout"])
async def test_cancelled_cleanup_observation_never_logs_private_failure(
    monkeypatch, tmp_path, caplog, boundary
):
    """Exercise the runtime shield/logger behavior without replacing asyncio."""
    caplog.set_level(logging.DEBUG)
    function, config = _function(monkeypatch, tmp_path)
    process = _Process()
    if boundary == "timeout":

        async def timed_out():
            process.entered.set()
            raise TimeoutError("controlled process deadline")

        monkeypatch.setattr(process, "wait", timed_out)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(bash.asyncio, "create_subprocess_shell", spawn)
    entered, release = asyncio.Event(), asyncio.Event()
    cleanup_tasks, completed = [], []
    failure = HomeAssistantError(f"PRIVATE_BASH_CLEANUP_MARKER_{boundary}")

    async def cleanup(owned_process, stdout, stderr, *, graceful):
        assert owned_process is process
        assert graceful is (boundary == "cancel")
        cleanup_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
        process.finish(-15)
        await asyncio.gather(stdout, stderr)
        completed.append(True)
        raise failure

    monkeypatch.setattr(bash, "_async_cleanup_process", cleanup)
    entity = SimpleNamespace()
    call = llm.ToolInput(
        id=f"private-{boundary}", tool_name="owned_shell", tool_args={}
    )

    async def invoke():
        with propagate_function_execution_errors():
            return await _invoke(function, config, entity, call)

    owner = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(process.entered.wait(), 1)
        if boundary == "cancel":
            owner.cancel("original caller cancellation")
            await asyncio.wait_for(entered.wait(), 1)
            remaining = 2
        else:
            await asyncio.wait_for(entered.wait(), 1)
            remaining = 3
        for index in range(remaining):
            owner.cancel(f"cancel during private cleanup {index}")
            await asyncio.sleep(0)
            assert not owner.done()
        assert completed == []
        release.set()
        with pytest.raises(HomeAssistantError) as caught:
            await owner
        assert caught.value is failure
        await asyncio.sleep(0)  # Deliver any runtime shield logging callback.
        assert completed == [True]
        assert len(cleanup_tasks) == 1
        spawn.assert_awaited_once()
        effects = entity._execution_effect_receipts[id(call)]
        assert len(effects) == 1 and effects[0].outcome == "uncertain"
        assert "PRIVATE_BASH_CLEANUP_MARKER" not in caplog.text
    finally:
        release.set()
        process.finish(-15)
        await asyncio.gather(owner, *cleanup_tasks, return_exceptions=True)
