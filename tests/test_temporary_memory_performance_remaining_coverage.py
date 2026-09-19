"""Regression coverage for Temporary Memory expiry persistence scheduling."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from custom_components.extended_openai_conversation_responses import (
    temporary_memory as performance,
)


class Manager(performance.TemporaryMemory):
    def __init__(self, save) -> None:
        super().__init__(None)
        self._async_save_locked = save


@pytest.mark.asyncio
async def test_pruned_state_save_runs_once_and_clears_completed_task() -> None:
    calls = 0

    async def save() -> None:
        nonlocal calls
        calls += 1

    manager = Manager(save)

    manager._schedule_pruned_state_save()
    task = manager._prune_save_task
    assert isinstance(task, asyncio.Task)

    await task
    await asyncio.sleep(0)

    assert calls == 1
    assert manager._prune_save_task is None


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

    manager._schedule_pruned_state_save()
    first = manager._prune_save_task
    await started.wait()

    manager._schedule_pruned_state_save()
    assert manager._prune_save_task is first
    assert calls == 1

    release.set()
    await first
    await asyncio.sleep(0)

    assert calls == 1
    assert manager._prune_save_task is None


@pytest.mark.asyncio
async def test_pruned_state_save_failure_is_contained_and_task_is_released(
    caplog,
) -> None:
    async def save() -> None:
        raise OSError("store unavailable")

    manager = Manager(save)

    manager._schedule_pruned_state_save()
    task = manager._prune_save_task

    await task
    await asyncio.sleep(0)

    assert "Unable to persist pruned temporary memories" in caplog.text
    assert manager._prune_save_task is None


@pytest.mark.asyncio
async def test_completed_save_does_not_clear_a_replacement_task() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    replacement_release = asyncio.Event()

    async def save() -> None:
        started.set()
        await release.wait()

    manager = Manager(save)
    manager._schedule_pruned_state_save()
    original = manager._prune_save_task
    await started.wait()

    replacement = asyncio.create_task(replacement_release.wait())
    manager._prune_save_task = replacement

    release.set()
    await original
    await asyncio.sleep(0)

    assert manager._prune_save_task is replacement

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

    manager._schedule_pruned_state_save()
    first = manager._prune_save_task
    await first
    await asyncio.sleep(0)

    manager._schedule_pruned_state_save()
    second = manager._prune_save_task
    assert second is not first
    await second
    await asyncio.sleep(0)

    assert calls == 2
    assert manager._prune_save_task is None
