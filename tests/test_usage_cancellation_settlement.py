"""Completed requests and runs settle accounting across repeated cancellation."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)
from tests.test_usage_accounting_recovery import FakeStorage


async def _wait_for_lock_waiter(lock):
    async with asyncio.timeout(1):
        while not lock._waiters:
            await asyncio.sleep(0)


async def _cancel_twice(task):
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()


@pytest.mark.parametrize("successful", [False, True])
async def test_completed_request_waits_for_accounting_lock_despite_cancellation(
    successful,
):
    details = FakeStorage()
    manager = UsageManager(FakeStorage(), FakeStorage(), details)
    await manager.async_initialize()
    runs = []
    dispatched = []

    async def request():
        async with manager.async_run() as run:
            runs.append(run)
            await manager.async_record_request(
                successful=successful,
                usage=RequestUsage(
                    input_tokens=120, output_tokens=30, total_tokens=150
                ),
                request_stage="after_tool",
                error_type=None if successful else "CancelledError",
            )
            dispatched.append("next tool")

    await manager._lock.acquire()
    task = asyncio.create_task(request())
    try:
        await _wait_for_lock_waiter(manager._lock)
        await _cancel_twice(task)
        assert manager.totals.api_request_count == 0
        manager._lock.release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert manager.totals.api_request_count == 1
        assert manager.totals.successful_request_count == int(successful)
        assert manager.totals.failed_request_count == int(not successful)
        assert manager.totals.total_tokens == 150
        assert manager.totals.conversation_count == 1
        assert len(details.data["requests"]) == 1
        assert len(details.data["runs"]) == 1
        recorded = details.data["requests"][0]
        assert recorded["run_id"] == runs[0].run_id
        assert recorded["successful"] is successful
        assert recorded["total_tokens"] == 150
        assert runs[0].request_count == 1
        assert runs[0].successful is False
        assert runs[0].error_type == "CancelledError"
        assert not manager._active_runs
        assert not dispatched
    finally:
        if manager._lock.locked():
            manager._lock.release()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.async_prepare_for_deletion()


async def test_request_finishes_all_saves_and_keeps_its_run_after_repeated_cancel():
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingStorage(FakeStorage):
        async def async_save(self, data):
            if not self.save_calls:
                started.set()
                await release.wait()
            await super().async_save(data)

    aggregates, details = BlockingStorage(), FakeStorage()
    manager = UsageManager(FakeStorage(), aggregates, details)
    await manager.async_initialize()
    runs = []

    async def request():
        async with manager.async_run() as run:
            runs.append(run)
            await manager.async_record_request(
                successful=False,
                usage=RequestUsage(total_tokens=23),
                error_type="ProviderStreamError",
            )

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await _cancel_twice(task)
        assert aggregates.data is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert aggregates.data["totals"]["api_request_count"] == 1
        assert aggregates.data["totals"]["total_tokens"] == 23
        assert aggregates.data["totals"]["conversation_count"] == 1
        assert len(details.data["requests"]) == len(details.data["runs"]) == 1
        assert details.data["requests"][0]["run_id"] == runs[0].run_id
        assert details.data["runs"][0]["request_count"] == 1
        assert details.data["runs"][0]["error_type"] == "CancelledError"
        settled = deepcopy(details.data)
        await asyncio.sleep(0)
        assert details.data == settled
        assert not manager._active_runs
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.async_prepare_for_deletion()


async def test_run_finalization_settles_before_repeated_cancel_propagates():
    details = FakeStorage()
    manager = UsageManager(FakeStorage(), FakeStorage(), details)
    await manager.async_initialize()
    runs = []

    async def request():
        async with manager.async_run() as run:
            runs.append(run)

    await manager._lock.acquire()
    task = asyncio.create_task(request())
    try:
        await _wait_for_lock_waiter(manager._lock)
        await _cancel_twice(task)
        manager._lock.release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert manager.totals.conversation_count == 1
        assert len(details.data["runs"]) == 1
        assert details.data["runs"][0]["run_id"] == runs[0].run_id
        assert runs[0].completed_at is not None
        assert not manager._active_runs
        await manager._async_finalize_run(runs[0])
        assert manager.totals.conversation_count == 1
        assert len(manager.runs) == 1
    finally:
        if manager._lock.locked():
            manager._lock.release()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.async_prepare_for_deletion()


async def test_shutdown_drains_request_settlement_before_flushing_usage():
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingStorage(FakeStorage):
        async def async_save(self, data):
            if not self.save_calls:
                started.set()
                await release.wait()
            await super().async_save(data)

    aggregates, details = BlockingStorage(), FakeStorage()
    manager = UsageManager(FakeStorage(), aggregates, details)
    await manager.async_initialize()

    async def request():
        async with manager.async_run():
            await manager.async_record_request(
                successful=True, usage=RequestUsage(total_tokens=17)
            )

    task = asyncio.create_task(request())
    shutdown = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        shutdown = asyncio.create_task(manager.async_shutdown())
        async with asyncio.timeout(1):
            while not task.cancelling():
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert not task.done()
        release.set()
        await asyncio.wait_for(shutdown, 1)
        assert task.cancelled()
        assert not manager._active_runs
        assert aggregates.data["totals"]["api_request_count"] == 1
        assert aggregates.data["totals"]["conversation_count"] == 1
        assert aggregates.data["totals"]["total_tokens"] == 17
        assert len(details.data["requests"]) == len(details.data["runs"]) == 1
        assert details.data["runs"][0]["error_type"] == "CancelledError"
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await manager.async_prepare_for_deletion()
