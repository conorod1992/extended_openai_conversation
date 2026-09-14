"""Focused coverage for Bash subprocess cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions import bash


@pytest.mark.asyncio
async def test_cleanup_cancels_and_awaits_pipe_readers_after_timeout(monkeypatch) -> None:
    """Readers still pending after the cleanup wait are cancelled and settled."""
    stdout_cancelled = asyncio.Event()
    stderr_cancelled = asyncio.Event()

    async def stubborn_reader(cancelled: asyncio.Event) -> tuple[bytes, bool]:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    stdout_task = asyncio.create_task(stubborn_reader(stdout_cancelled))
    stderr_task = asyncio.create_task(stubborn_reader(stderr_cancelled))
    # Let both tasks enter their coroutine before cleanup cancels them. Cancelling a
    # task before its first scheduling turn prevents the coroutine body/finally block
    # from running, which would make this assertion depend on event-loop timing.
    await asyncio.sleep(0)
    process = SimpleNamespace(pid=1234, returncode=0)

    wait_for_calls = 0

    async def timeout_without_cancelling(awaitable, *, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        assert timeout == bash._SHELL_CLEANUP_WAIT_SECONDS
        raise TimeoutError

    monkeypatch.setattr(bash.os, "name", "nt")
    monkeypatch.setattr(bash.asyncio, "wait_for", timeout_without_cancelling)

    await bash._async_cleanup_process(
        process,
        stdout_task,
        stderr_task,
        graceful=False,
    )

    assert wait_for_calls == 1
    assert stdout_task.cancelled()
    assert stderr_task.cancelled()
    assert stdout_cancelled.is_set()
    assert stderr_cancelled.is_set()
