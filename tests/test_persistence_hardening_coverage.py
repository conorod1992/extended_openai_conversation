"""Focused coverage for persistence transaction recovery boundaries."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import persistence_hardening as ph


class _DummyManager:
    """Small manager exercising the generic persistence guard contract."""

    def __init__(self) -> None:
        self.value = "initial"
        self.initialize_error: BaseException | None = None
        self.save_error: BaseException | None = None
        self.save_started: asyncio.Event | None = None
        self.save_release: asyncio.Event | None = None

    async def async_initialize(self) -> str:
        if self.initialize_error is not None:
            self.value = "partially-loaded"
            raise self.initialize_error
        return "initialized"

    async def _async_save_locked(self) -> str:
        if self.save_started is not None:
            self.save_started.set()
        if self.save_release is not None:
            await self.save_release.wait()
        if self.save_error is not None:
            raise self.save_error
        return "saved"


def _install_dummy_guard() -> None:
    ph._install_manager_guard(
        _DummyManager,
        lambda manager: manager.value,
        lambda manager, snapshot: setattr(manager, "value", snapshot),
        lambda manager: setattr(manager, "value", "reset"),
    )


def test_repair_private_store_mode_changes_only_insecure_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chmod_calls: list[tuple[str, int]] = []
    current_mode = ph._PRIVATE_STORE_MODE
    fake_os = SimpleNamespace(
        stat=lambda _path: SimpleNamespace(st_mode=current_mode),
        chmod=lambda path, mode: chmod_calls.append((path, mode)),
    )
    monkeypatch.setattr(ph, "os", fake_os)

    ph._repair_private_store_mode("/config/.storage/already-private")
    assert chmod_calls == []

    fake_os.stat = lambda _path: SimpleNamespace(st_mode=0o644)
    ph._repair_private_store_mode("/config/.storage/insecure")
    assert chmod_calls == [
        ("/config/.storage/insecure", ph._PRIVATE_STORE_MODE)
    ]


def test_repair_private_store_mode_ignores_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(_path: str) -> Any:
        raise FileNotFoundError

    fake_os = SimpleNamespace(
        stat=_missing,
        chmod=lambda *_args: pytest.fail("missing files must not be chmodded"),
    )
    monkeypatch.setattr(ph, "os", fake_os)

    ph._repair_private_store_mode("/config/.storage/not-created-yet")


@pytest.mark.asyncio
async def test_manager_guard_resets_failed_initialization_and_remains_retryable() -> None:
    _install_dummy_guard()
    manager = _DummyManager()
    manager.initialize_error = RuntimeError("load failed")

    with pytest.raises(RuntimeError, match="load failed"):
        await manager.async_initialize()

    assert manager.value == "reset"
    assert not hasattr(manager, ph._COMMITTED_STATE)

    manager.initialize_error = None
    manager.value = "retry-state"
    assert await manager.async_initialize() == "initialized"
    assert getattr(manager, ph._COMMITTED_STATE) == "retry-state"


@pytest.mark.asyncio
async def test_manager_guard_rolls_back_when_underlying_save_task_is_cancelled() -> None:
    _install_dummy_guard()
    manager = _DummyManager()
    await manager.async_initialize()
    manager.value = "uncommitted"
    manager.save_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await manager._async_save_locked()

    assert manager.value == "initial"
    assert getattr(manager, ph._COMMITTED_STATE) == "initial"


@pytest.mark.asyncio
async def test_manager_guard_caller_cancellation_wins_over_later_save_failure() -> None:
    _install_dummy_guard()
    manager = _DummyManager()
    await manager.async_initialize()
    manager.value = "uncommitted"
    manager.save_started = asyncio.Event()
    manager.save_release = asyncio.Event()
    manager.save_error = OSError("disk unavailable")

    operation = asyncio.create_task(manager._async_save_locked())
    await manager.save_started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    manager.save_release.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await operation

    assert isinstance(exc_info.value.__cause__, OSError)
    assert manager.value == "initial"
    assert getattr(manager, ph._COMMITTED_STATE) == "initial"


@pytest.mark.asyncio
async def test_manager_guard_defers_cancellation_until_successful_save_commits() -> None:
    _install_dummy_guard()
    manager = _DummyManager()
    await manager.async_initialize()
    manager.value = "new-durable-state"
    manager.save_started = asyncio.Event()
    manager.save_release = asyncio.Event()

    operation = asyncio.create_task(manager._async_save_locked())
    await manager.save_started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    manager.save_release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert manager.value == "new-durable-state"
    assert getattr(manager, ph._COMMITTED_STATE) == "new-durable-state"


def test_reset_memory_clears_all_runtime_and_committed_state() -> None:
    manager = SimpleNamespace(
        _memories={"one": object()},
        _token_index=defaultdict(set, {"token": {"one"}}),
        _key_index={"key": "one"},
        _embedding_cache={"one": [1.0]},
        _embedding_cache_dirty=True,
        _initialized=True,
    )
    setattr(manager, ph._COMMITTED_STATE, {"memories": {"one": object()}})

    ph._reset_memory(manager)

    assert manager._memories == {}
    assert isinstance(manager._token_index, defaultdict)
    assert manager._key_index == {}
    assert manager._embedding_cache == {}
    assert manager._embedding_cache_dirty is False
    assert manager._initialized is False
    assert not hasattr(manager, ph._COMMITTED_STATE)


def test_reset_knowledge_clears_indexes_and_committed_state() -> None:
    manager = SimpleNamespace(
        _sources={"source": object()},
        _chunks={"chunk": object()},
        _token_index=defaultdict(set, {"token": {"chunk"}}),
        _initialized=True,
    )
    setattr(manager, ph._COMMITTED_STATE, {"sources": {"source": object()}})

    ph._reset_knowledge(manager)

    assert manager._sources == {}
    assert manager._chunks == {}
    assert isinstance(manager._token_index, defaultdict)
    assert manager._initialized is False
    assert not hasattr(manager, ph._COMMITTED_STATE)


def test_reset_temporary_memory_clears_counter_and_committed_state() -> None:
    manager = SimpleNamespace(
        _records={"record": object()},
        expired_pruned=7,
        _initialized=True,
    )
    setattr(manager, ph._COMMITTED_STATE, {"records": {"record": object()}})

    ph._reset_temporary_memory(manager)

    assert manager._records == {}
    assert manager.expired_pruned == 0
    assert manager._initialized is False
    assert not hasattr(manager, ph._COMMITTED_STATE)


def test_reset_request_rules_restores_defaults_with_or_without_committed_state() -> None:
    compile_calls: list[int] = []
    manager = SimpleNamespace(
        _defaults={"custom": True},
        _wording_groups=[{"custom": True}],
        _rules=[{"custom": True}],
        _initialized=True,
        _sort_and_compile=lambda: compile_calls.append(1),
    )

    ph._reset_request_rules(manager)
    assert manager._defaults == dict(ph.DEFAULT_MATCHING)
    assert manager._wording_groups == list(ph.DEFAULT_WORDING_GROUPS)
    assert manager._rules == []
    assert manager._initialized is False
    assert compile_calls == [1]

    setattr(manager, ph._COMMITTED_STATE, {"rules": ["old"]})
    manager._initialized = True
    ph._reset_request_rules(manager)
    assert manager._initialized is False
    assert compile_calls == [1, 1]
    assert not hasattr(manager, ph._COMMITTED_STATE)
