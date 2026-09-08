"""Regression tests for Guest Mode persistence transactions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    GuestModeSchedule,
)


def _manager(hass) -> GuestModeManager:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    return manager


def _active_schedule() -> GuestModeSchedule:
    return GuestModeSchedule(
        active_from="2026-09-08T00:00:00+00:00",
        active_until=None,
        source="home_assistant",
        updated_at="2026-09-08T00:00:00+00:00",
    )


async def test_failed_guest_mode_disable_keeps_committed_live_policy(hass) -> None:
    manager = _manager(hass)
    committed = _active_schedule()
    manager._schedule = committed
    manager._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    listener = Mock()
    manager.async_add_listener(listener)

    with pytest.raises(OSError, match="disk full"):
        await manager.async_disable_trusted()

    assert manager.schedule == committed
    listener.assert_not_called()


async def test_failed_guest_mode_update_keeps_previous_schedule(hass) -> None:
    manager = _manager(hass)
    committed = _active_schedule()
    manager._schedule = committed
    manager._store.async_save = AsyncMock(side_effect=OSError("write failed"))

    with pytest.raises(OSError, match="write failed"):
        await manager.async_update_trusted(
            active_from="2026-09-09T00:00:00+00:00",
            active_until="2026-09-10T00:00:00+00:00",
        )

    assert manager.schedule == committed


async def test_failed_guest_mode_restore_keeps_previous_schedule(hass) -> None:
    manager = _manager(hass)
    committed = _active_schedule()
    manager._schedule = committed
    manager._store.async_save = AsyncMock(side_effect=OSError("restore write failed"))
    replacement = GuestModeSchedule(
        active_from="2026-09-12T00:00:00+00:00",
        active_until="2026-09-13T00:00:00+00:00",
        source="restore",
        updated_at="2026-09-08T01:00:00+00:00",
    )

    with pytest.raises(OSError, match="restore write failed"):
        await manager.async_replace_backup(replacement)

    assert manager.schedule == committed


async def test_guest_mode_mutations_serialize_monotonic_restrictions(hass) -> None:
    manager = _manager(hass)
    first_save_entered = asyncio.Event()
    release_first_save = asyncio.Event()
    saved: list[dict] = []
    now = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)

    async def save(data: dict) -> None:
        saved.append(data)
        if len(saved) == 1:
            first_save_entered.set()
            await release_first_save.wait()

    manager._store.async_save = save

    first = asyncio.create_task(
        manager.async_restrict(
            active_from="2026-09-08T12:00:00+00:00",
            active_until="2026-09-08T14:00:00+00:00",
            now=now,
        )
    )
    await first_save_entered.wait()
    second = asyncio.create_task(
        manager.async_restrict(
            active_from="2026-09-08T10:00:00+00:00",
            active_until="2026-09-08T11:00:00+00:00",
            now=now,
        )
    )
    await asyncio.sleep(0)

    assert len(saved) == 1
    release_first_save.set()
    await asyncio.gather(first, second)

    assert manager.schedule is not None
    assert manager.schedule.active_from == "2026-09-08T10:00:00+00:00"
    assert manager.schedule.active_until == "2026-09-08T14:00:00+00:00"
    assert len(saved) == 2


async def test_guest_mode_cancellation_waits_for_known_persistence_outcome(hass) -> None:
    manager = _manager(hass)
    committed = _active_schedule()
    manager._schedule = committed
    save_entered = asyncio.Event()
    release_save = asyncio.Event()

    async def save(_data: dict) -> None:
        save_entered.set()
        await release_save.wait()

    manager._store.async_save = save
    listener = Mock()
    manager.async_add_listener(listener)

    task = asyncio.create_task(manager.async_disable_trusted())
    await save_entered.wait()
    assert manager.schedule == committed
    listener.assert_not_called()

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release_save.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.schedule is None
    listener.assert_called_once_with()
