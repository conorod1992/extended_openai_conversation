"""Transient Script cleanup owns its outcome through repeated cancellation."""

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.script_lifecycle import (
    async_cleanup_script,
)


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
