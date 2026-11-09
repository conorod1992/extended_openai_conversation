"""Transient Script cleanup owns its outcome through repeated cancellation."""

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.script_lifecycle import (
    async_cleanup_script,
    async_run_script,
)


@pytest.mark.parametrize("run_outcome", ["success", "error", "cancel"])
@pytest.mark.parametrize("stop_outcome", ["success", "error", "cancel"])
@pytest.mark.parametrize("cancels", [1, 3])
async def test_cancelled_run_settles_native_execution_and_stop(
    run_outcome, stop_outcome, cancels, caplog
):
    running, stopping, release, finished = (asyncio.Event() for _ in range(4))
    effects = []
    native = None

    async def execute():
        effects.append(True)
        running.set()
        try:
            await release.wait()
            if run_outcome == "error":
                raise ValueError("PRIVATE_EXECUTION_FAILURE")
            if run_outcome == "cancel":
                raise asyncio.CancelledError("PRIVATE_NATIVE_CANCELLATION")
            return "stopped"
        finally:
            finished.set()

    async def run(variables, context):
        nonlocal native
        assert variables == {"argument": 1} and context == "context"
        native = asyncio.create_task(execute())
        return await asyncio.shield(native)

    async def stop():
        stopping.set()
        await release.wait()
        await finished.wait()
        if stop_outcome == "error":
            raise RuntimeError("PRIVATE_STOP_FAILURE")
        if stop_outcome == "cancel":
            raise asyncio.CancelledError("PRIVATE_STOP_CANCELLATION")

    task = asyncio.create_task(async_run_script(
        SimpleNamespace(async_run=run, async_stop=stop), {"argument": 1}, "context"
    ))
    try:
        await running.wait()
        task.cancel("original cancellation")
        await stopping.wait()
        for _ in range(cancels - 1):
            task.cancel("later cancellation")
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await task
        assert native.done() and finished.is_set()
        assert effects == [True]
        assert "PRIVATE_" not in caplog.text
        assert ("Script execution failed during cancellation" in caplog.text) is (run_outcome == "error")
        assert ("Script stop failed during cancellation" in caplog.text) is (stop_outcome != "success")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if native is not None:
            await asyncio.gather(native, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_uncancelled_run_preserves_native_outcome_and_context(outcome):
    owner = ContextVar("script_owner")
    state = {}
    failure = ValueError("native failure")

    async def run(variables, context):
        assert owner.get() is state
        await asyncio.sleep(0)
        state["finished"] = True
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise asyncio.CancelledError("native cancellation")
        return variables

    async def stop():
        state["stop"] = True

    token = owner.set(state)
    try:
        script = SimpleNamespace(async_run=run, async_stop=stop)
        if outcome == "success":
            assert await async_run_script(script, {"result": 42}, None) == {"result": 42}
        elif outcome == "error":
            with pytest.raises(ValueError) as caught:
                await async_run_script(script, {}, None)
            assert caught.value is failure
        else:
            with pytest.raises(asyncio.CancelledError, match="native cancellation"):
                await async_run_script(script, {}, None)
        assert state["finished"]
        assert "stop" not in state  # An already settled native run needs no stop.
    finally:
        owner.reset(token)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("already_cancelled", [False, True])
@pytest.mark.parametrize("fail_cleanup", [False, True])
@pytest.mark.parametrize("cancels", [0, 3])
async def test_cleanup_settles_once_before_request_outcome(
    fallback, already_cancelled, fail_cleanup, cancels, caplog
):
    entered, release = asyncio.Event(), asyncio.Event()
    private_context = ContextVar("cleanup_context", default="missing")
    completed = []
    original = asyncio.CancelledError("original cancellation")
    cleanup_error = RuntimeError("PRIVATE_CLEANUP_FAILURE")

    async def cleanup():
        assert private_context.get() == "owning context"
        entered.set()
        await release.wait()
        completed.append(True)
        if fail_cleanup:
            raise cleanup_error

    runner = SimpleNamespace(**{"async_stop" if fallback else "async_unload": cleanup})

    async def run():
        token = private_context.set("owning context")
        try:
            try:
                if already_cancelled:
                    raise original
                return "completed"
            finally:
                await async_cleanup_script(runner, cancelled=already_cancelled)
        finally:
            private_context.reset(token)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for _ in range(cancels):
            task.cancel("cancel during cleanup")
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        if already_cancelled or cancels:
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            if already_cancelled:
                assert caught.value is original
            else:
                assert caught.value.args == ("cancel during cleanup",)
        elif fail_cleanup:
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is cleanup_error
        else:
            assert await task == "completed"
        assert completed == [True]
        if fail_cleanup and (already_cancelled or cancels):
            assert "Script cleanup failed during cancellation" in caplog.text
        assert "PRIVATE_CLEANUP_FAILURE" not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("already_cancelled", [False, True])
async def test_independently_cancelled_cleanup_terminates(already_cancelled):
    original = asyncio.CancelledError("original cancellation")
    attempts = []

    async def cleanup():
        attempts.append(True)
        raise asyncio.CancelledError("cleanup cancelled itself")

    async def run():
        try:
            if already_cancelled:
                raise original
        finally:
            await async_cleanup_script(
                SimpleNamespace(async_unload=cleanup), cancelled=already_cancelled
            )

    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(run(), 1)
    if already_cancelled:
        assert caught.value is original
    else:
        assert caught.value.args == ("cleanup cancelled itself",)
    assert attempts == [True]


async def test_handled_outer_cancellation_does_not_mask_cleanup_failure():
    async def cleanup():
        raise RuntimeError("new cleanup failure")

    try:
        raise asyncio.CancelledError("already handled")
    except asyncio.CancelledError:
        with pytest.raises(RuntimeError, match="new cleanup failure"):
            await async_cleanup_script(SimpleNamespace(async_unload=cleanup))


@pytest.mark.parametrize("stop_outcome", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("cancels", [0, 3])
async def test_preexisting_stop_settles_then_unloads_even_on_stop_failure(
    stop_outcome, cancels, caplog
):
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    failure = RuntimeError("PRIVATE_STOP_FAILURE")

    async def stop():
        entered.set()
        await release.wait()
        order.append("stop")
        if stop_outcome == "failed":
            raise failure
        if stop_outcome == "cancelled":
            raise asyncio.CancelledError("independent stop cancellation")

    async def unload():
        order.append("unload")

    pending_stop = asyncio.create_task(stop())
    task = asyncio.create_task(
        async_cleanup_script(
            SimpleNamespace(async_unload=unload), pending_stop=pending_stop
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for _ in range(cancels):
            task.cancel("cancel during stop")
            await asyncio.sleep(0)
        assert not task.done()
        assert order == []
        release.set()
        if cancels or stop_outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            if cancels:
                assert caught.value.args == ("cancel during stop",)
        elif stop_outcome == "failed":
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is failure
        else:
            await task
        assert order == ["stop", "unload"]
        assert pending_stop.done()
        assert "PRIVATE_STOP_FAILURE" not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, pending_stop, return_exceptions=True)
