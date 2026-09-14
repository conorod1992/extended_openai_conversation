"""Regression coverage for Temporary Memory expiry persistence scheduling."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from custom_components.extended_openai_conversation_responses import (
    temporary_memory_performance as performance,
)


class Manager:
    def __init__(self, save) -> None:
        self._lock = asyncio.Lock()
        self._async_save_locked = save


@pytest.mark.asyncio
async def test_pruned_state_save_runs_once_and_clears_completed_task() -> None:
    calls = 0

    async def save() -> None:
        nonlocal calls
        calls += 1

    manager = Manager(save)

    performance._schedule_pruned_state_save(manager)
    task = getattr(manager, performance._PRUNE_SAVE_TASK)
    assert isinstance(task, asyncio.Task)

    await task
    await asyncio.sleep(0)

    assert calls == 1
    assert getattr(manager, performance._PRUNE_SAVE_TASK) is None


@pytest.mark.asyncio
async def test_pruned_state_save_coalesces_while_existing_task_is_pending() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def save() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    manager = Manager(save)

    performance._schedule_pruned_state_save(manager)
    first = getattr(manager, performance._PRUNE_SAVE_TASK)
    await started.wait()

    performance._schedule_pruned_state_save(manager)
    assert getattr(manager, performance._PRUNE_SAVE_TASK) is first
    assert calls == 1

    release.set()
    await first
    await asyncio.sleep(0)

    assert calls == 1
    assert getattr(manager, performance._PRUNE_SAVE_TASK) is None


@pytest.mark.asyncio
async def test_pruned_state_save_failure_is_contained_and_task_is_released(caplog) -> None:
    async def save() -> None:
        raise OSError("store unavailable")

    manager = Manager(save)

    performance._schedule_pruned_state_save(manager)
    task = getattr(manager, performance._PRUNE_SAVE_TASK)

    await task
    await asyncio.sleep(0)

    assert "Unable to persist pruned temporary memories" in caplog.text
    assert getattr(manager, performance._PRUNE_SAVE_TASK) is None


@pytest.mark.asyncio
async def test_completed_save_does_not_clear_a_replacement_task() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    replacement_release = asyncio.Event()

    async def save() -> None:
        started.set()
        await release.wait()

    manager = Manager(save)
    performance._schedule_pruned_state_save(manager)
    original = getattr(manager, performance._PRUNE_SAVE_TASK)
    await started.wait()

    replacement = asyncio.create_task(replacement_release.wait())
    setattr(manager, performance._PRUNE_SAVE_TASK, replacement)

    release.set()
    await original
    await asyncio.sleep(0)

    assert getattr(manager, performance._PRUNE_SAVE_TASK) is replacement

    replacement.cancel()
    with suppress(asyncio.CancelledError):
        await replacement


@pytest.mark.asyncio
async def test_scheduler_starts_new_save_after_previous_task_finished() -> None:
    calls = 0

    async def save() -> None:
        nonlocal calls
        calls += 1

    manager = Manager(save)

    performance._schedule_pruned_state_save(manager)
    first = getattr(manager, performance._PRUNE_SAVE_TASK)
    await first
    await asyncio.sleep(0)

    performance._schedule_pruned_state_save(manager)
    second = getattr(manager, performance._PRUNE_SAVE_TASK)
    assert second is not first
    await second
    await asyncio.sleep(0)

    assert calls == 2
    assert getattr(manager, performance._PRUNE_SAVE_TASK) is None
