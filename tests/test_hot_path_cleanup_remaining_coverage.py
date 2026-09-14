"""Residual coverage for hot-path cleanup fallback behavior."""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import (
    debug,
    hot_path_cleanup,
    lifecycle_optimizations as lifecycle,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager


def _register_usage_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure direct installer mutations are restored after each test."""
    monkeypatch.setattr(UsageManager, "_async_save_safely", UsageManager._async_save_safely)
    monkeypatch.setattr(
        lifecycle, "_async_prune_usage_if_due", lifecycle._async_prune_usage_if_due
    )


@pytest.mark.asyncio
async def test_lazy_usage_save_falls_back_when_delay_save_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed delayed-save schedule falls back to the established persistence path."""
    _register_usage_mutations(monkeypatch)
    previous = AsyncMock()
    monkeypatch.setattr(UsageManager, "_async_save_safely", previous)

    class FailingStorage:
        def async_delay_save(self, _callback, _delay) -> None:
            raise RuntimeError("scheduler unavailable")

    hot_path_cleanup._install_usage_lazy_snapshots_and_background_prune()
    manager = SimpleNamespace(
        _storage=FailingStorage(),
        _daily_storage=SimpleNamespace(),
        _detail_storage=SimpleNamespace(),
    )
    save = object()

    with caplog.at_level(logging.ERROR):
        await UsageManager._async_save_safely(manager, "request totals", save)

    previous.assert_awaited_once_with(manager, "request totals", save)
    assert "falling back to existing persistence" in caplog.text


@pytest.mark.asyncio
async def test_non_transactional_prune_runs_in_named_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy non-transactional pruning is moved off the response-completion path."""
    _register_usage_mutations(monkeypatch)

    async def non_transactional_prune_details(_manager) -> None:
        return None

    prune = AsyncMock()
    monkeypatch.setattr(UsageManager, "async_prune_details", non_transactional_prune_details)
    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", prune)

    hot_path_cleanup._install_usage_lazy_snapshots_and_background_prune()
    manager = SimpleNamespace()

    await lifecycle._async_prune_usage_if_due(manager)

    task = getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK)
    assert isinstance(task, asyncio.Task)
    assert task.get_name() == "extended_openai_usage_retention_maintenance"
    await task
    await asyncio.sleep(0)

    prune.assert_awaited_once_with(manager)
    assert getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("already_pruned", [True, False], ids=["today", "task-running"])
async def test_background_prune_deduplicates_work(
    monkeypatch: pytest.MonkeyPatch, already_pruned: bool
) -> None:
    """The wrapper skips both today's completed work and an existing live task."""
    _register_usage_mutations(monkeypatch)

    async def non_transactional_prune_details(_manager) -> None:
        return None

    prune = AsyncMock()
    monkeypatch.setattr(UsageManager, "async_prune_details", non_transactional_prune_details)
    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", prune)
    hot_path_cleanup._install_usage_lazy_snapshots_and_background_prune()

    if already_pruned:
        manager = SimpleNamespace(
            **{lifecycle._LAST_USAGE_PRUNE_DATE: dt_util.utcnow().date().isoformat()}
        )
        await lifecycle._async_prune_usage_if_due(manager)
        assert not hasattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK)
    else:
        release = asyncio.Event()

        async def wait_forever() -> None:
            await release.wait()

        current = asyncio.create_task(wait_forever())
        manager = SimpleNamespace(**{hot_path_cleanup._USAGE_PRUNE_TASK: current})
        try:
            await lifecycle._async_prune_usage_if_due(manager)
            assert getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK) is current
        finally:
            release.set()
            await current

    prune.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_prune_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Maintenance failures are logged and never escape the background task."""
    _register_usage_mutations(monkeypatch)

    async def non_transactional_prune_details(_manager) -> None:
        return None

    prune = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(UsageManager, "async_prune_details", non_transactional_prune_details)
    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", prune)
    hot_path_cleanup._install_usage_lazy_snapshots_and_background_prune()
    manager = SimpleNamespace()

    with caplog.at_level(logging.ERROR):
        await lifecycle._async_prune_usage_if_due(manager)
        task = getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK)
        await task
        await asyncio.sleep(0)

    assert task.exception() is None
    assert "Background usage retention maintenance failed" in caplog.text
    assert getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK) is None


@pytest.mark.asyncio
async def test_background_prune_callback_preserves_newer_task_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older completion callback cannot clear a replacement maintenance task."""
    _register_usage_mutations(monkeypatch)

    async def non_transactional_prune_details(_manager) -> None:
        return None

    started = asyncio.Event()
    release = asyncio.Event()

    async def prune(_manager) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(UsageManager, "async_prune_details", non_transactional_prune_details)
    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", prune)
    hot_path_cleanup._install_usage_lazy_snapshots_and_background_prune()
    manager = SimpleNamespace()

    await lifecycle._async_prune_usage_if_due(manager)
    first = getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK)
    await started.wait()
    replacement = object()
    setattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK, replacement)

    release.set()
    await first
    await asyncio.sleep(0)

    assert getattr(manager, hot_path_cleanup._USAGE_PRUNE_TASK) is replacement


def test_debug_event_records_first_action_latency_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action latency is captured on the first tool/search event and remains stable."""
    monkeypatch.setattr(debug.DebugProviderRequest, "add_event", debug.DebugProviderRequest.add_event)
    hot_path_cleanup._install_debug_single_conversion()
    request = debug.DebugProviderRequest(
        request_id="request",
        api_surface="responses",
        started_at=dt_util.utcnow().isoformat(),
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=time.monotonic() - 0.05,
    )

    request.add_event({"type": "response.function_call.arguments.delta", "delta": "{}"})
    first_action_ms = request.first_action_ms
    request.add_event({"type": "response.web_search_call.completed"})

    assert first_action_ms is not None
    assert request.first_action_ms == first_action_ms
    assert len(request.response_events) == 2
    assert request.response_events[0]["type"] == "response.function_call.arguments.delta"
