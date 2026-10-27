"""An asynchronous registration cannot publish or wake another registration."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import conversation
from tests.test_agent_configuration_lifecycle import startup as startup


def _remove(startup):
    callbacks = startup.removals[:]
    startup.removals.clear()
    for callback in callbacks:
        callback()


@pytest.mark.parametrize("stage", ["required", "optional"])
async def test_removed_initialization_cannot_publish_resources(startup, stage):
    entered = asyncio.Event()
    release = asyncio.Event()
    getter = startup.getters["usage" if stage == "required" else "archive"]
    result = getter.return_value

    async def initialize(*_args):
        entered.set()
        await release.wait()
        return result

    getter.side_effect = initialize
    task = asyncio.create_task(startup.agent.async_added_to_hass())
    await entered.wait()
    ready = startup.agent._agent_ready
    _remove(startup)
    retired_while_initializing = startup.agent._agent_initialization_failed
    assert not ready.is_set()
    release.set()
    await task
    try:
        assert retired_while_initializing
        assert startup.agent._agent_initialization_failed
        assert startup.scheduled == []
        conversation.conversation.async_set_agent.assert_not_called()
    finally:
        _remove(startup)


@pytest.mark.parametrize("old_outcome", ["success", "failure", "cancellation"])
async def test_old_initialization_cannot_settle_new_registration(
    startup, monkeypatch, old_outcome
):
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def initialize():
        nonlocal calls
        index = calls
        calls += 1
        entered[index].set()
        await release[index].wait()
        if index == 0:
            if old_outcome == "failure":
                raise OSError("old initialization failed")
            if old_outcome == "cancellation":
                raise asyncio.CancelledError

    monkeypatch.setattr(startup.agent, "_async_initialize_agent_state", initialize)
    monkeypatch.setattr(
        startup.agent, "_async_initialize_optional_managers", AsyncMock()
    )
    old = asyncio.create_task(startup.agent.async_added_to_hass())
    await entered[0].wait()
    old_ready = startup.agent._agent_ready
    old_callbacks = startup.removals[:]
    _remove(startup)
    new = asyncio.create_task(startup.agent.async_added_to_hass())
    await entered[1].wait()
    new_ready = startup.agent._agent_ready
    release[0].set()
    old_result = await asyncio.gather(old, return_exceptions=True)
    try:
        if old_outcome == "failure":
            assert isinstance(old_result[0], OSError)
        elif old_outcome == "cancellation":
            assert isinstance(old_result[0], asyncio.CancelledError)
        else:
            assert old_result == [None]
        assert old_ready.is_set()
        assert startup.agent._agent_ready is new_ready
        assert not new_ready.is_set()
        assert not startup.agent._agent_initialization_failed
        for callback in old_callbacks:
            callback()
        assert not startup.agent._agent_initialization_failed
        assert not new_ready.is_set()
        assert startup.scheduled == []
    finally:
        release[1].set()
        await new
    try:
        assert new_ready.is_set()
        assert not startup.agent._agent_initialization_failed
        assert len(startup.scheduled) == 1
    finally:
        _remove(startup)
    startup.scheduled[0][3].assert_called_once_with()


@pytest.mark.parametrize("replacements", [1, 2])
async def test_waiter_cannot_cross_registration_identity(startup, replacements):
    entered = asyncio.Event()

    class ObservedReady(asyncio.Event):
        async def wait(self):
            entered.set()
            return await super().wait()

    first = startup.agent._agent_ready = ObservedReady()
    request = SimpleNamespace(text="", language="en", conversation_id=None)
    old = asyncio.create_task(startup.agent._async_process(request))
    await entered.wait()
    for _ in range(replacements):
        startup.agent._agent_ready = asyncio.Event()
    startup.agent._agent_ready.set()
    startup.agent._agent_initialization_failed = False
    first.set()
    stale = await old
    assert stale.response.error_code is not None
    current = await startup.agent._async_process(request)
    assert current.response.error_code is None


async def test_retirement_during_maintenance_admission_skips_initialization(
    startup, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def admission(_agent):
        entered.set()
        await release.wait()
        yield

    monkeypatch.setattr(conversation, "conversation_request_lease", admission)
    task = asyncio.create_task(startup.agent.async_added_to_hass())
    await entered.wait()
    _remove(startup)
    release.set()
    await task
    conversation.ConversationEntity.async_added_to_hass.assert_not_awaited()
    for getter in startup.getters.values():
        getter.assert_not_awaited()
    assert startup.scheduled == []
    assert startup.agent._agent_ready.is_set()
    assert startup.agent._agent_initialization_failed


@pytest.mark.parametrize("cancellations", [0, 2])
async def test_removal_drains_startup_before_cleanup_and_cancellation(
    startup, monkeypatch, cancellations
):
    initializing, release, removing = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    usage = startup.getters["usage"].return_value

    async def load_usage(*_args):
        initializing.set()
        await release.wait()
        return usage

    startup.getters["usage"].side_effect = load_usage
    initializing_task = asyncio.create_task(startup.agent.async_added_to_hass())
    await initializing.wait()
    ready = startup.agent._agent_ready
    original_wait = ready.wait

    async def wait():
        removing.set()
        return await original_wait()

    monkeypatch.setattr(ready, "wait", wait)
    parent_cleanup = AsyncMock()
    unregister, remove_runtime = Mock(), Mock()
    monkeypatch.setattr(
        conversation.ConversationEntity, "async_will_remove_from_hass", parent_cleanup
    )
    monkeypatch.setattr(conversation, "unregister_legacy_agent", unregister)
    monkeypatch.setattr(conversation, "remove_function_group_runtime", remove_runtime)
    _remove(startup)
    task = asyncio.create_task(startup.agent.async_will_remove_from_hass())
    await removing.wait()
    try:
        assert not task.done()
        parent_cleanup.assert_not_awaited()
        for _ in range(cancellations):
            task.cancel()
            # Deliver each cancellation before the next one; no wall-clock delay.
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        await initializing_task
        if cancellations:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert ready.is_set()
        assert startup.scheduled == []
        unregister.assert_called_once()
        remove_runtime.assert_called_once()
        parent_cleanup.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(initializing_task, task, return_exceptions=True)
