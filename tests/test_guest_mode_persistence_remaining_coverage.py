"""Regression coverage for Guest Mode persistence and schedule lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    GuestModeSchedule,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        {"schedule": {"active_from": "not-a-timestamp"}},
        {
            "schedule": {
                "active_from": "2026-09-14T10:00:00",
                "active_until": None,
                "source": "home_assistant",
                "updated_at": None,
            }
        },
        {
            "schedule": {
                "active_from": "2026-09-14T10:00:00+00:00",
                "active_until": "not-a-timestamp",
                "source": "home_assistant",
                "updated_at": None,
            }
        },
        {"schedule": {"unexpected": "shape"}},
    ],
)
async def test_initialize_ignores_malformed_persisted_schedule(hass, stored) -> None:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(async_load=AsyncMock(return_value=stored))

    await manager.async_initialize()

    assert manager.schedule is None
    assert manager._initialized is True


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
    assert saved_payloads == [{"schedule": {
        "active_from": manager.schedule.active_from,
        "active_until": manager.schedule.active_until,
        "source": "home_assistant",
        "updated_at": manager.schedule.updated_at,
    }}]


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
        now="2026-09-14T09:00:00+00:00",
    )

    assert manager.schedule is not None
    assert manager.schedule.active_from == "2026-09-14T10:00:00+00:00"
    assert manager.schedule.active_until == "2026-09-14T11:00:00+00:00"
    assert manager.schedule.source == "llm"
    assert status["state"] == "scheduled"
