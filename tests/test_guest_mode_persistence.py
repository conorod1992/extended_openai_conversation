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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        {"schedule": {"active_from": "not-a-timestamp"}},
        {
            "schedule": {
                "active_from": "2026-09-14T10:00:00+00:00",
                "active_until": "not-a-timestamp",
                "source": "home_assistant",
                "updated_at": None,
            }
        },
        {"schedule": {"unexpected": "shape"}},
        {"schedule": {"active_from": "2026-09-13T10:00:00+00:00", "extra": True}},
        ["legacy", "payload"],
    ],
)
async def test_initialize_ignores_malformed_persisted_schedule(hass, caplog, stored) -> None:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_load=AsyncMock(return_value=stored))

    await manager.async_initialize()

    assert manager.schedule is None
    assert manager._initialized is True
    if isinstance(stored, dict) and isinstance(stored.get("schedule"), dict):
        assert "Ignoring malformed Guest Mode state" in caplog.text


@pytest.mark.asyncio
async def test_initialize_loads_valid_persisted_schedule_only_once(hass) -> None:
    stored = {
        "schedule": {
            "active_from": "2026-09-14T10:00:00+00:00",
            "active_until": "2026-09-14T12:00:00+00:00",
            "source": "home_assistant",
            "updated_at": "2026-09-14T09:00:00+00:00",
        }
    }
    load = AsyncMock(return_value=stored)
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_load=load)

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.schedule == GuestModeSchedule(**stored["schedule"])
    load.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_initialize_retries_failed_load_then_restores_valid_schedule_once(hass) -> None:
    stored = {
        "schedule": {
            "active_from": "2026-09-14T10:00:00+00:00",
            "active_until": None,
            "source": "home_assistant",
            "updated_at": "2026-09-14T09:00:00+00:00",
        }
    }
    load = AsyncMock(side_effect=[OSError("store unavailable"), stored])
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_load=load)

    with pytest.raises(OSError, match="store unavailable"):
        await manager.async_initialize()
    assert manager._initialized is False
    assert manager.schedule is None

    await manager.async_initialize()
    assert manager._initialized is True
    assert manager.schedule == GuestModeSchedule(**stored["schedule"])

    await manager.async_initialize()
    assert load.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_update_waits_for_save_then_publishes_and_reraises(hass) -> None:
    save_started = asyncio.Event()
    allow_save = asyncio.Event()
    saved_payloads: list[dict] = []

    async def save(payload):
        saved_payloads.append(payload)
        save_started.set()
        await allow_save.wait()

    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_save=save)
    manager._initialized = True
    notifications: list[bool] = []
    manager.async_add_listener(lambda: notifications.append(True))

    update = asyncio.create_task(
        manager.async_update_trusted(
            active_from="2026-09-14T10:00:00+00:00",
            active_until="2026-09-14T12:00:00+00:00",
        )
    )
    await save_started.wait()
    update.cancel()
    await asyncio.sleep(0)

    assert not update.done()
    assert manager.schedule is None
    assert notifications == []

    allow_save.set()
    with pytest.raises(asyncio.CancelledError):
        await update

    assert manager.schedule is not None
    assert manager.schedule.active_from == "2026-09-14T10:00:00+00:00"
    assert manager.schedule.active_until == "2026-09-14T12:00:00+00:00"
    assert notifications == [True]
    assert saved_payloads == [
        {
            "schedule": {
                "active_from": manager.schedule.active_from,
                "active_until": manager.schedule.active_until,
                "source": "home_assistant",
                "updated_at": manager.schedule.updated_at,
            }
        }
    ]


@pytest.mark.asyncio
async def test_cancelled_update_with_save_failure_keeps_old_schedule(hass) -> None:
    save_started = asyncio.Event()
    allow_failure = asyncio.Event()

    async def failing_save(_payload):
        save_started.set()
        await allow_failure.wait()
        raise OSError("disk unavailable")

    manager = GuestModeManager(hass, "entry", "agent")
    old_schedule = GuestModeSchedule(
        active_from="2026-09-14T08:00:00+00:00",
        active_until="2026-09-14T09:00:00+00:00",
        source="home_assistant",
        updated_at="2026-09-14T07:00:00+00:00",
    )
    manager._schedule = old_schedule
    manager._store = SimpleNamespace(async_save=failing_save)
    manager._initialized = True
    notifications: list[bool] = []
    manager.async_add_listener(lambda: notifications.append(True))

    update = asyncio.create_task(
        manager.async_update_trusted(
            active_from="2026-09-14T10:00:00+00:00",
            active_until="2026-09-14T12:00:00+00:00",
        )
    )
    await save_started.wait()
    update.cancel()
    await asyncio.sleep(0)
    allow_failure.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await update

    assert isinstance(exc_info.value.__cause__, OSError)
    assert manager.schedule is old_schedule
    assert notifications == []


@pytest.mark.asyncio
async def test_cancelled_store_task_does_not_publish_schedule(hass) -> None:
    async def cancelled_save(_payload):
        raise asyncio.CancelledError

    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_save=cancelled_save)
    manager._initialized = True

    with pytest.raises(asyncio.CancelledError):
        await manager.async_update_trusted(
            active_from="2026-09-14T10:00:00+00:00",
            active_until="2026-09-14T12:00:00+00:00",
        )

    assert manager.schedule is None


@pytest.mark.asyncio
async def test_restrict_replaces_expired_schedule_instead_of_widening_it(hass) -> None:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_save=AsyncMock())
    manager._initialized = True
    manager._schedule = GuestModeSchedule(
        active_from="2026-09-14T06:00:00+00:00",
        active_until="2026-09-14T07:00:00+00:00",
        source="home_assistant",
        updated_at="2026-09-14T05:00:00+00:00",
    )

    status = await manager.async_restrict(
        active_from="2026-09-14T10:00:00+00:00",
        active_until="2026-09-14T11:00:00+00:00",
        now=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )

    assert manager.schedule is not None
    assert manager.schedule.active_from == "2026-09-14T10:00:00+00:00"
    assert manager.schedule.active_until == "2026-09-14T11:00:00+00:00"
    assert manager.schedule.source == "llm"
    assert status["state"] == "scheduled"
