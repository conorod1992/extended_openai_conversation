"""Pruning serializes survivors off-loop before publishing durable changes."""

import asyncio
from datetime import timedelta
import threading

import pytest

from custom_components.extended_openai_conversation_responses import usage
from homeassistant.util import dt as dt_util
from tests.test_usage_detail_lifecycle import _manager, _request, _run


@pytest.mark.parametrize("scenario", ["tiny", "large", "unchanged", "failed"])
async def test_prune_survivors_worker_and_transaction(monkeypatch, scenario):
    loop_thread = threading.get_ident()
    threads = set()
    convert = usage.asdict

    def checked(record):
        threads.add(threading.get_ident())
        return convert(record)

    monkeypatch.setattr(usage, "asdict", checked)
    now = dt_util.utcnow()
    size = 30000 if scenario != "tiny" else 1
    run_count = 10000 if scenario != "tiny" else 1
    recent = now.isoformat()
    requests = [_request(str(i), recent) for i in range(size)]
    runs = [_run(str(i), recent) for i in range(run_count)]
    expired = (
        []
        if scenario == "unchanged"
        else [_request("expired", (now - timedelta(days=366)).isoformat())]
    )
    original_requests = requests + expired
    saved = []

    class Store:
        async def async_save(self, data):
            assert manager.requests is original_requests
            assert manager.runs is runs
            assert manager._lock.locked()
            await asyncio.sleep(0)
            if scenario == "failed":
                raise OSError("write failed")
            saved.append(data)

    manager = _manager(Store(), request_retention_days=365, run_retention_days=365)
    manager.requests = original_requests
    manager.runs = runs
    if scenario == "failed":
        bookkeeping = (
            manager._detail_retention_dirty,
            manager._last_prune_date,
            manager._next_prune_retry,
        )
        with pytest.raises(OSError, match="write failed"):
            await manager.async_prune_details()
        assert manager.requests is original_requests and manager.runs is runs
        assert (
            manager._detail_retention_dirty,
            manager._last_prune_date,
            manager._next_prune_retry,
        ) == bookkeeping
    else:
        result = await manager.async_prune_details()
        assert result == {"deleted_requests": len(expired), "deleted_runs": 0}
        assert manager.requests == requests and manager.runs == runs
    if scenario == "unchanged":
        assert not saved and not threads
    else:
        assert threads and loop_thread not in threads
        if scenario != "failed":
            assert len(saved) == 1
            assert len(saved[0]["requests"]) == size
            assert len(saved[0]["runs"]) == run_count


async def test_pruning_allows_loop_work_while_survivor_conversion_is_pending(
    monkeypatch,
):
    from tests.test_usage_detail_lifecycle import DetailStorage

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    started = asyncio.Event()
    release = threading.Event()
    convert = usage.asdict
    first = True

    def gated(record):
        nonlocal first
        if first and threading.get_ident() != loop_thread:
            first = False
            loop.call_soon_threadsafe(started.set)
            assert release.wait(10), "test did not release worker"
        return convert(record)

    monkeypatch.setattr(usage, "asdict", gated)
    manager = _manager(
        DetailStorage(), request_retention_days=365, run_retention_days=365
    )
    now = dt_util.utcnow()
    manager.requests = [_request(str(i), now.isoformat()) for i in range(30000)]
    manager.requests.append(
        _request("expired", (now - timedelta(days=366)).isoformat())
    )
    manager.runs = [_run(str(i), now.isoformat()) for i in range(10000)]
    original = manager.requests
    task = asyncio.create_task(manager.async_prune_details())
    try:
        await asyncio.wait_for(started.wait(), 5)
        heartbeat = loop.create_future()
        loop.call_soon(heartbeat.set_result, True)
        assert await heartbeat
        assert not task.done() and manager.requests is original
    finally:
        release.set()
        result = await task
    assert result == {"deleted_requests": 1, "deleted_runs": 0}
    assert len(manager.requests) == 30000 and len(manager.runs) == 10000
