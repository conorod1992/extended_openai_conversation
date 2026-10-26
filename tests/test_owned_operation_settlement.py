"""Equivalent owned-operation contracts exercised through their existing seams."""

import asyncio
from contextvars import ContextVar

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    AgentMaintenanceGate,
    _async_run_exclusive_operation,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    _async_settle_native_edit,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    _async_settle_store_io,
)


@pytest.fixture(params=["store", "file", "maintenance"])
def owned_runner(request):
    """Retain each call site's real lock/lease placement around the await."""
    gate = AgentMaintenanceGate()
    lock = asyncio.Lock()

    async def run(operation):
        if request.param == "maintenance":
            return await _async_run_exclusive_operation(gate, operation)
        async with lock:
            settle = (
                _async_settle_store_io
                if request.param == "store"
                else _async_settle_native_edit
            )
            return await settle(operation())

    return run


@pytest.mark.parametrize("outcome", ["success", "failure", "self_cancel"])
@pytest.mark.parametrize("cancels", [0, 1, 3])
@pytest.mark.parametrize("effect_before_wait", [False, True])
async def test_owned_operation_settles_once_with_original_outcome(
    owned_runner, outcome, cancels, effect_before_wait
):
    entered, release = asyncio.Event(), asyncio.Event()
    private_context = ContextVar("settlement_owner", default=None)
    marker = object()
    result = object()
    failure = OSError("PRIVATE_NATIVE_FAILURE")
    effects, attempts = [], []
    observed = []

    async def operation():
        assert private_context.get() is marker
        attempts.append(True)
        if effect_before_wait:
            effects.append(True)
        entered.set()
        await release.wait()
        if outcome == "failure":
            raise failure
        if outcome == "self_cancel":
            raise asyncio.CancelledError("native cancellation")
        if not effect_before_wait:
            effects.append(True)
        return result

    async def owner():
        token = private_context.set(marker)
        try:
            return await owned_runner(operation)
        finally:
            private_context.reset(token)

    async def follower():
        async def inspect():
            observed.append(tuple(effects))

        await owned_runner(inspect)

    task = asyncio.create_task(owner())
    waiting = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        waiting = asyncio.create_task(follower())
        await asyncio.sleep(0)
        assert not waiting.done()
        for index in range(cancels):
            task.cancel(f"caller cancellation {index}")
            await asyncio.sleep(0)
            assert not task.done()
            assert not waiting.done()
        release.set()
        if outcome == "failure":
            with pytest.raises(OSError) as caught:
                await task
            assert caught.value is failure
        elif outcome == "self_cancel" or cancels:
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            if outcome == "success":
                assert caught.value.args == ("caller cancellation 0",)
        else:
            assert await task is result
        await asyncio.wait_for(waiting, 1)
        expected = (True,) if effect_before_wait or outcome == "success" else ()
        assert observed == [expected]
        assert attempts == [True]
        assert tuple(effects) == expected
        assert private_context.get() is None
    finally:
        release.set()
        await asyncio.gather(
            task, *([waiting] if waiting else []), return_exceptions=True
        )


async def test_cancelled_waiter_never_dispatches(owned_runner):
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = []

    async def first():
        entered.set()
        await release.wait()

    async def forbidden():
        attempts.append(True)

    owner = asyncio.create_task(owned_runner(first))
    waiter = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(owned_runner(forbidden))
        await asyncio.sleep(0)
        waiter.cancel("before dispatch")
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert attempts == []
        release.set()
        await asyncio.wait_for(owner, 1)
        await owned_runner(forbidden)
        assert attempts == [True]
    finally:
        release.set()
        await asyncio.gather(
            owner, *([waiter] if waiter else []), return_exceptions=True
        )


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("settle", [_async_settle_store_io, _async_settle_native_edit])
async def test_already_settled_future_has_no_extra_task_or_loop_turn(settle, outcome):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    result = object()
    failure = OSError("PRIVATE_SETTLED_FAILURE")
    if outcome == "failure":
        future.set_exception(failure)
    elif outcome == "cancelled":
        future.cancel("native future cancellation")
    else:
        future.set_result(result)
    tasks = asyncio.all_tasks()
    next_turn = []
    loop.call_soon(next_turn.append, True)
    if outcome == "failure":
        with pytest.raises(OSError) as caught:
            await settle(future)
        assert caught.value is failure
    elif outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError, match="native future cancellation"):
            await settle(future)
    else:
        assert await settle(future) is result
    assert next_turn == []
    assert asyncio.all_tasks() == tasks


@pytest.mark.parametrize("settle", [_async_settle_store_io, _async_settle_native_edit])
async def test_independent_owned_work_remains_concurrent(settle):
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def operation(index):
        entered[index].set()
        await release.wait()
        return index

    tasks = [asyncio.create_task(settle(operation(index))) for index in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(item.wait() for item in entered)), 1)
        assert not any(task.done() for task in tasks)
        release.set()
        assert await asyncio.gather(*tasks) == [0, 1]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
