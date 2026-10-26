"""Invocation cleanup preserves outcomes and only settles its own retrieval."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.request_context import (
    _ACTIVE_INVOCATION,
    RequestInvocation,
    conversation_invocation,
    set_temporary_memory_prefetch,
)


async def test_nested_invocation_restores_exact_outer_owner_before_returning():
    started = asyncio.Event()
    parent = asyncio.create_task(asyncio.Event().wait())
    metadata = {"owner": "parent"}
    outer = RequestInvocation(metadata, parent)
    token = _ACTIVE_INVOCATION.set(outer)

    async def read():
        started.set()
        await asyncio.Event().wait()

    try:
        async with conversation_invocation({"owner": "child"}):
            assert _ACTIVE_INVOCATION.get().metadata == {"owner": "child"}
            assert _ACTIVE_INVOCATION.get().temporary_memory_prefetch is None
            child = asyncio.create_task(read())
            set_temporary_memory_prefetch(child)
            await started.wait()
        assert child.cancelled()
        assert not parent.done()
        assert _ACTIVE_INVOCATION.get() is outer
        assert metadata == {"owner": "parent"}
    finally:
        parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
        _ACTIVE_INVOCATION.reset(token)


@pytest.mark.parametrize("primary_failure", [False, True])
async def test_repeated_cancellation_drains_owned_read_and_preserves_primary_error(
    primary_failure,
):
    read_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    original = RuntimeError("primary request failure")
    reads = []

    async def read():
        read_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    async def request():
        async with conversation_invocation():
            task = asyncio.create_task(read())
            reads.append(task)
            set_temporary_memory_prefetch(task)
            await read_started.wait()
            if primary_failure:
                raise original
            return "completed request"

    task = asyncio.create_task(request())
    try:
        await cleanup_started.wait()
        task.cancel("first cleanup cancellation")
        # Deliver the first cancellation before issuing a distinct second one.
        await asyncio.sleep(0)
        assert not task.done()
        assert not reads[0].done()
        task.cancel("second cleanup cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        release_cleanup.set()
        if primary_failure:
            with pytest.raises(RuntimeError) as error:
                await task
            assert error.value is original
        else:
            with pytest.raises(asyncio.CancelledError) as error:
                await task
            assert error.value.args == ("first cleanup cancellation",)
        assert reads[0].cancelled()
        assert _ACTIVE_INVOCATION.get().temporary_memory_prefetch is None
    finally:
        release_cleanup.set()
        await asyncio.gather(task, *reads, return_exceptions=True)


@pytest.mark.parametrize("primary_failure", [False, True])
async def test_abandoned_read_error_does_not_replace_request_outcome(
    primary_failure, caplog
):
    original = RuntimeError("request outcome")
    answer = object()
    read_task = None

    async def read():
        raise ValueError("private retrieval error must not be logged")

    async def request():
        nonlocal read_task
        async with conversation_invocation():
            read_task = asyncio.create_task(read())
            set_temporary_memory_prefetch(read_task)
            await asyncio.wait({read_task})
            if primary_failure:
                raise original
            return answer

    if primary_failure:
        with pytest.raises(RuntimeError) as error:
            await request()
        assert error.value is original
    else:
        assert await request() is answer
    assert read_task.done()
    assert _ACTIVE_INVOCATION.get().temporary_memory_prefetch is None
    assert "private retrieval error" not in caplog.text
