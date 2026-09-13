"""Focused coverage for lifecycle optimization safety and fallback paths."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    lifecycle_optimizations as lifecycle,
)


class _MutatingLock:
    """Async lock stand-in that mutates manager state on acquisition."""

    def __init__(self, callback: Any) -> None:
        self._callback = callback

    async def __aenter__(self) -> None:
        self._callback()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def test_usage_snapshot_rejects_unknown_category() -> None:
    """Programming errors must not silently persist the wrong usage shape."""
    with pytest.raises(ValueError, match="Unknown usage persistence category"):
        lifecycle._usage_snapshot(SimpleNamespace(), "unknown")


async def test_prune_due_fast_paths_skip_duplicate_and_cooldown_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daily and retry guards should avoid acquiring the manager lock at all."""
    today = lifecycle.dt_util.utcnow().date().isoformat()
    prune_calls = 0

    def _prune(_manager: Any) -> dict[str, int]:
        nonlocal prune_calls
        prune_calls += 1
        return {"deleted_requests": 0, "deleted_runs": 0}

    monkeypatch.setattr(lifecycle, "_prune_usage_locked", _prune)

    duplicate = SimpleNamespace()
    setattr(duplicate, lifecycle._LAST_USAGE_PRUNE_DATE, today)
    duplicate._lock = _MutatingLock(lambda: None)
    await lifecycle._async_prune_usage_if_due(duplicate)

    cooling_down = SimpleNamespace()
    setattr(
        cooling_down,
        lifecycle._NEXT_USAGE_PRUNE_RETRY,
        lifecycle.time.monotonic() + 60.0,
    )
    cooling_down._lock = _MutatingLock(lambda: None)
    await lifecycle._async_prune_usage_if_due(cooling_down)

    assert prune_calls == 0


async def test_prune_due_rechecks_guards_after_lock_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing coroutine winning the lock must prevent duplicate prune work."""
    today = lifecycle.dt_util.utcnow().date().isoformat()
    prune_calls = 0

    def _prune(_manager: Any) -> dict[str, int]:
        nonlocal prune_calls
        prune_calls += 1
        return {"deleted_requests": 0, "deleted_runs": 0}

    monkeypatch.setattr(lifecycle, "_prune_usage_locked", _prune)

    duplicate = SimpleNamespace(_detail_storage=None)
    duplicate._lock = _MutatingLock(
        lambda: setattr(duplicate, lifecycle._LAST_USAGE_PRUNE_DATE, today)
    )
    await lifecycle._async_prune_usage_if_due(duplicate)

    cooling_down = SimpleNamespace(_detail_storage=None)
    cooling_down._lock = _MutatingLock(
        lambda: setattr(
            cooling_down,
            lifecycle._NEXT_USAGE_PRUNE_RETRY,
            lifecycle.time.monotonic() + 60.0,
        )
    )
    await lifecycle._async_prune_usage_if_due(cooling_down)

    assert prune_calls == 0


async def test_prune_due_immediate_save_fallback_clears_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store without delayed-save support still persists pruned details."""
    saves = 0

    async def _save_details() -> None:
        nonlocal saves
        saves += 1

    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _detail_storage=object(),
        _async_save_details=_save_details,
        requests=[],
        runs=[],
    )
    setattr(manager, lifecycle._USAGE_PRUNE_SAVE_PENDING, True)
    monkeypatch.setattr(
        lifecycle,
        "_prune_usage_locked",
        lambda _manager: {"deleted_requests": 0, "deleted_runs": 0},
    )
    monkeypatch.setattr(lifecycle, "_schedule_store_snapshot", lambda *_args: False)

    await lifecycle._async_prune_usage_if_due(manager)

    assert saves == 1
    assert getattr(manager, lifecycle._USAGE_PRUNE_SAVE_PENDING) is False
    assert getattr(manager, lifecycle._NEXT_USAGE_PRUNE_RETRY) == 0.0
    assert getattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE) == (
        lifecycle.dt_util.utcnow().date().isoformat()
    )


async def test_prune_due_failure_preserves_pending_save_and_sets_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed detail persistence must be retried rather than marked successful."""

    async def _save_details() -> None:
        raise OSError("disk unavailable")

    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _detail_storage=object(),
        _async_save_details=_save_details,
        requests=[],
        runs=[],
    )
    monkeypatch.setattr(
        lifecycle,
        "_prune_usage_locked",
        lambda _manager: {"deleted_requests": 1, "deleted_runs": 0},
    )
    monkeypatch.setattr(lifecycle, "_schedule_store_snapshot", lambda *_args: False)
    before = lifecycle.time.monotonic()

    with pytest.raises(OSError, match="disk unavailable"):
        await lifecycle._async_prune_usage_if_due(manager)

    assert getattr(manager, lifecycle._USAGE_PRUNE_SAVE_PENDING) is True
    assert getattr(manager, lifecycle._NEXT_USAGE_PRUNE_RETRY) >= (
        before + lifecycle._USAGE_PRUNE_RETRY_SECONDS
    )
    assert getattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE, None) is None


def _install_usage_wrappers(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    """Install wrappers around harmless stand-ins and return the captured wrappers."""
    from custom_components.extended_openai_conversation_responses.usage import UsageManager

    async def _original_save(manager: Any, label: str, save: Any) -> None:
        manager.original_save_calls.append((label, save))

    async def _original_finalize(manager: Any, run: Any) -> None:
        manager.original_finalize_calls.append(run)

    async def _original_prune(manager: Any, *, save: bool = True) -> dict[str, int]:
        return {"deleted_requests": -1, "deleted_runs": -1}

    async def _original_clear(manager: Any, *, confirm: bool) -> dict[str, int]:
        return {"deleted_requests": -1, "deleted_runs": -1}

    monkeypatch.setattr(UsageManager, "_async_save_safely", _original_save)
    monkeypatch.setattr(UsageManager, "_async_finalize_run", _original_finalize)
    monkeypatch.setattr(UsageManager, "async_prune_details", _original_prune)
    monkeypatch.setattr(UsageManager, "async_clear_details", _original_clear)

    lifecycle._install_usage_persistence()
    return (
        UsageManager._async_save_safely,
        UsageManager._async_finalize_run,
        UsageManager.async_prune_details,
        UsageManager.async_clear_details,
    )


async def test_usage_persistence_wrappers_fall_back_and_prune_after_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling failures use immediate persistence and finalization still prunes."""
    wrapped_save, wrapped_finalize, _wrapped_prune, _wrapped_clear = (
        _install_usage_wrappers(monkeypatch)
    )
    manager = SimpleNamespace(
        original_save_calls=[],
        original_finalize_calls=[],
        _storage=object(),
        _daily_storage=object(),
        _detail_storage=object(),
    )

    def _aggregate_failure(_manager: Any) -> bool:
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(
        lifecycle, "_schedule_usage_aggregate_snapshots", _aggregate_failure
    )
    await wrapped_save(manager, "request aggregates", "save-request")
    assert manager.original_save_calls == [("request aggregates", "save-request")]

    monkeypatch.setattr(
        lifecycle,
        "_usage_snapshot",
        lambda _manager, category: {"category": category},
    )

    def _store_failure(*_args: Any) -> bool:
        raise RuntimeError("store scheduler failed")

    monkeypatch.setattr(lifecycle, "_schedule_store_snapshot", _store_failure)
    await wrapped_save(manager, "request details", "save-details")
    await wrapped_save(manager, "unrecognized label", "save-unknown")
    assert manager.original_save_calls[-2:] == [
        ("request details", "save-details"),
        ("unrecognized label", "save-unknown"),
    ]

    prune_calls = 0

    async def _prune_if_due(_manager: Any) -> None:
        nonlocal prune_calls
        prune_calls += 1

    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", _prune_if_due)
    await wrapped_finalize(manager, "run-1")
    assert manager.original_finalize_calls == ["run-1"]
    assert prune_calls == 1


async def test_usage_prune_and_clear_wrappers_cover_save_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit maintenance mutates details atomically and resets retry state."""
    _save, _finalize, wrapped_prune, wrapped_clear = _install_usage_wrappers(monkeypatch)
    save_calls = 0

    async def _save_details() -> None:
        nonlocal save_calls
        save_calls += 1

    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _detail_storage=object(),
        _async_save_details=_save_details,
        requests=[1, 2],
        runs=[3],
    )
    monkeypatch.setattr(
        lifecycle,
        "_prune_usage_locked",
        lambda _manager: {"deleted_requests": 1, "deleted_runs": 1},
    )

    result = await wrapped_prune(manager, save=False)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert save_calls == 0
    assert getattr(manager, lifecycle._USAGE_PRUNE_SAVE_PENDING) is False

    result = await wrapped_prune(manager, save=True)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert save_calls == 1

    with pytest.raises(ValueError, match="Explicit confirmation"):
        await wrapped_clear(manager, confirm=False)
    assert manager.requests == [1, 2]
    assert manager.runs == [3]

    cleared = await wrapped_clear(manager, confirm=True)
    assert cleared == {"deleted_requests": 2, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []
    assert save_calls == 2


def test_usage_install_preserves_transactional_detail_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist-first UsageManager implementations retain their own detail methods."""
    from custom_components.extended_openai_conversation_responses.usage import UsageManager

    async def _save(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _finalize(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _persist_first_prune(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {}

    async def _clear(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {}

    setattr(_persist_first_prune, "_extended_openai_persist_first", True)
    monkeypatch.setattr(UsageManager, "_async_save_safely", _save)
    monkeypatch.setattr(UsageManager, "_async_finalize_run", _finalize)
    monkeypatch.setattr(UsageManager, "async_prune_details", _persist_first_prune)
    monkeypatch.setattr(UsageManager, "async_clear_details", _clear)

    lifecycle._install_usage_persistence()

    assert UsageManager.async_prune_details is _persist_first_prune
    assert UsageManager.async_clear_details is _clear


def _install_memory_wrappers(
    monkeypatch: pytest.MonkeyPatch,
    retrieve_memories: Any,
    retrieve_temporary: Any,
    owner: Any,
) -> tuple[Any, Any, Any]:
    """Install memory wrappers around controlled retrieval stand-ins."""
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )
    from custom_components.extended_openai_conversation_responses import (
        temporary_memory_ownership as ownership,
    )

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_async_retrieve_memories", retrieve_memories
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_retrieve_temporary_memories",
        retrieve_temporary,
    )
    monkeypatch.setattr(ownership, "_owner_from_resolved_scope", lambda _scope: owner)
    lifecycle._install_memory_prefetch()
    return (
        ExtendedOpenAIAgentEntity._async_retrieve_memories,
        ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories,
        ownership,
    )


async def test_memory_prefetch_binds_owner_and_is_consumed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporary retrieval overlaps persistent retrieval under the resolved owner."""
    started = asyncio.Event()
    release = asyncio.Event()
    observed_owner: list[Any] = []

    async def _temporary(_agent: Any) -> list[str]:
        from custom_components.extended_openai_conversation_responses import (
            temporary_memory_ownership as ownership,
        )

        observed_owner.append(ownership._ACTIVE_OWNER_SCOPE_ID.get())
        started.set()
        await release.wait()
        return ["temporary"]

    async def _memories(_agent: Any, *args: Any, **kwargs: Any) -> list[str]:
        await started.wait()
        assert args == ("query",)
        assert kwargs == {"limit": 2}
        return ["persistent"]

    wrapped_memories, wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, "owner-1"
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await wrapped_memories(agent, "query", limit=2) == ["persistent"]
        task = lifecycle._TEMPORARY_MEMORY_PREFETCH.get()
        assert task is not None
        assert observed_owner == ["owner-1"]

        release.set()
        assert await wrapped_temporary(agent) == ["temporary"]
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        pending = lifecycle._TEMPORARY_MEMORY_PREFETCH.get()
        if pending is not None and not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)


async def test_memory_prefetch_is_cancelled_when_persistent_retrieval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed main retrieval must not leak its speculative temporary-memory task."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _temporary(_agent: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _memories(_agent: Any) -> None:
        await started.wait()
        raise RuntimeError("memory lookup failed")

    wrapped_memories, _wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, None
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        with pytest.raises(RuntimeError, match="memory lookup failed"):
            await wrapped_memories(agent)
        assert cancelled.is_set()
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)


async def test_temporary_memory_wrapper_uses_direct_and_existing_task_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct retrieval is preserved, while an existing prefetch is reused exactly once."""
    direct_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _temporary(_agent: Any, *args: Any, **kwargs: Any) -> Any:
        direct_calls.append((args, kwargs))
        return "direct"

    async def _memories(_agent: Any) -> str:
        return "persistent"

    wrapped_memories, wrapped_temporary, _ownership = _install_memory_wrappers(
        monkeypatch, _memories, _temporary, None
    )
    agent = SimpleNamespace(_temporary_memory=object())
    token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        assert await wrapped_temporary(agent, "arg", key="value") == "direct"
        assert direct_calls == [(("arg",), {"key": "value"})]

        existing = asyncio.create_task(asyncio.sleep(0, result="prefetched"))
        lifecycle._TEMPORARY_MEMORY_PREFETCH.set(existing)
        assert await wrapped_memories(agent) == "persistent"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is existing
        assert await wrapped_temporary(agent) == "prefetched"
        assert lifecycle._TEMPORARY_MEMORY_PREFETCH.get() is None
        assert direct_calls == [(("arg",), {"key": "value"})]
    finally:
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(token)
