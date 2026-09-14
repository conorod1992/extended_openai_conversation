"""Cancellation and rollback coverage for transactional persistence guards."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    _COMMITTED_STATE,
    _install_manager_guard,
)


SaveImpl = Callable[[Any], Awaitable[Any]]


def _install_test_guard(save_impl: SaveImpl) -> type[Any]:
    """Create one isolated manager class using the production persistence guard."""

    class Manager:
        def __init__(self) -> None:
            self.value = "committed"
            self.persisted: str | None = None
            self.reset = False

        async def async_initialize(self) -> None:
            return None

        async def _async_save_locked(self) -> Any:
            return await save_impl(self)

    def snapshot(manager: Any) -> str:
        return manager.value

    def restore(manager: Any, snapshot: str) -> None:
        manager.value = snapshot

    def reset(manager: Any) -> None:
        manager.reset = True

    _install_manager_guard(Manager, snapshot, restore, reset)
    return Manager


async def test_caller_cancellation_waits_for_successful_save_before_propagating() -> None:
    """Caller cancellation must not interrupt a Store write already in progress."""
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def save(manager: Any) -> str:
        save_started.set()
        await release_save.wait()
        manager.persisted = manager.value
        return "saved"

    manager_type = _install_test_guard(save)
    manager = manager_type()
    await manager.async_initialize()
    manager.value = "staged"

    operation = asyncio.create_task(manager._async_save_locked())
    await save_started.wait()
    operation.cancel()
    await asyncio.sleep(0)

    # The caller has asked to cancel, but the shielded durability boundary must
    # remain alive until the write outcome is known.
    assert not operation.done()
    release_save.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    assert manager.persisted == "staged"
    assert manager.value == "staged"
    assert getattr(manager, _COMMITTED_STATE) == "staged"


async def test_cancelled_save_task_restores_last_committed_snapshot() -> None:
    """Cancellation of the save itself must roll live state back atomically."""

    async def save(_manager: Any) -> None:
        raise asyncio.CancelledError

    manager_type = _install_test_guard(save)
    manager = manager_type()
    await manager.async_initialize()
    manager.value = "staged"

    with pytest.raises(asyncio.CancelledError):
        await manager._async_save_locked()

    assert manager.value == "committed"
    assert getattr(manager, _COMMITTED_STATE) == "committed"


async def test_caller_cancellation_plus_save_failure_rolls_back_before_cancel() -> None:
    """A failed write still rolls back when caller cancellation races with it."""
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def save(_manager: Any) -> None:
        save_started.set()
        await release_save.wait()
        raise OSError("store unavailable")

    manager_type = _install_test_guard(save)
    manager = manager_type()
    await manager.async_initialize()
    manager.value = "staged"

    operation = asyncio.create_task(manager._async_save_locked())
    await save_started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    assert not operation.done()

    release_save.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await operation

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "store unavailable"
    assert manager.value == "committed"
    assert getattr(manager, _COMMITTED_STATE) == "committed"
