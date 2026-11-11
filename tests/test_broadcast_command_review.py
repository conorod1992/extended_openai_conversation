"""Independent review of policy changes during Broadcast admission awaits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    broadcast_command,
    intercom,
)
from tests.test_conversation_orchestration import _pipeline_fixture


@pytest.mark.parametrize(
    ("boundary", "change"),
    [
        ("manager_load", "guest"),
        ("authorization", "guest"),
        ("authorization", "shortcut"),
    ],
)
async def test_policy_tightening_before_admission_never_queues_or_retries(
    hass, monkeypatch, change, boundary
):
    entity, _, _, _, process = _pipeline_fixture(monkeypatch, text="Broadcast Dinner")
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    manager._store.async_save = AsyncMock()
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_: ["assist_satellite.kitchen"]
    )
    monkeypatch.setattr(manager, "_schedule_drain", lambda _: None)
    monkeypatch.setattr(intercom, "async_call_later", lambda *_: lambda: None)
    entered, release = asyncio.Event(), asyncio.Event()
    active = False
    entity._effective_guest_policy = lambda: SimpleNamespace(guest_active=active)

    async def wait():
        entered.set()
        await release.wait()

    async def get_manager(_hass):
        if boundary == "manager_load":
            await wait()
        return manager

    async def get_user(_user_id):
        if boundary == "authorization":
            await wait()
        return SimpleNamespace(
            is_active=True,
            is_admin=True,
            permissions=SimpleNamespace(check_entity=lambda *_: True),
        )

    monkeypatch.setattr(broadcast_command, "async_get_intercom", get_manager)
    hass.auth.async_get_user = get_user
    sent = AsyncMock(wraps=manager.async_send)
    manager.async_send = sent
    pending = asyncio.create_task(process())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == "guest":
            active = True
        else:
            await manager.async_set_builtin_voice_command(False)
        release.set()
        result = await pending
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await manager.async_shutdown()

    assert result.response.error_code is not None
    assert manager.history() == []
    assert sent.await_count == int(boundary == "authorization")
    entity._async_handle_message_with_ha_tools.assert_not_awaited()
    assert entity.hass.bus.async_fire.call_args.args[1]["status"] == "error"


@pytest.mark.parametrize("first_setting", ["master", "shortcut"])
@pytest.mark.parametrize("write_fails", [False, True])
async def test_cancelled_settings_write_keeps_lock_until_disk_and_memory_agree(
    hass, first_setting, write_fails
):
    manager = intercom.IntercomManager(hass)
    disk = {"enabled": True, "builtin_voice_command": True, "other": "keep"}
    entered, release = asyncio.Event(), asyncio.Event()
    writes, workers = [], []

    async def save(value):
        writes.append(dict(value))
        number = len(writes)

        async def write():
            if number == 1:
                entered.set()
                await release.wait()
                if write_fails:
                    raise HomeAssistantError("disk full")
            disk.clear()
            disk.update(value)

        worker = asyncio.create_task(write())
        workers.append(worker)
        # HA's executor file write can survive cancellation of its waiter.
        await asyncio.shield(worker)

    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value=dict(disk)), async_save=save
    )
    await manager.async_initialize()
    master = manager.async_set_enabled
    shortcut = manager.async_set_builtin_voice_command
    a, b = (master, shortcut) if first_setting == "master" else (shortcut, master)
    first = asyncio.create_task(a(False))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        first.cancel()
        await asyncio.sleep(0)
        second = asyncio.create_task(b(False))
        await asyncio.sleep(0)
        assert not first.done(), (
            "a cancelled waiter must still own its unfinished write"
        )
        assert len(writes) == 1, "another setting cannot overwrite unsettled state"
        release.set()
        with pytest.raises(
            HomeAssistantError if write_fails else asyncio.CancelledError
        ):
            await first
        await second
        assert disk == {
            "enabled": write_fails and first_setting == "master",
            "builtin_voice_command": write_fails and first_setting == "shortcut",
            "other": "keep",
        }
        assert manager.enabled is disk["enabled"]
        assert manager.builtin_voice_command is disk["builtin_voice_command"]
        assert manager._settings == disk
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )
        await asyncio.gather(*workers, return_exceptions=True)
        await manager.async_shutdown()


async def test_live_guest_rejection_does_not_load_broadcast(hass, monkeypatch):
    get = AsyncMock(side_effect=AssertionError("must remain lazy"))
    monkeypatch.setattr(broadcast_command, "async_get_intercom", get)
    with pytest.raises(HomeAssistantError):
        await broadcast_command.async_try_broadcast_command(
            hass,
            SimpleNamespace(text="Broadcast Dinner"),
            {},
            guest_active=False,
            live_guest_active=lambda: True,
        )
    get.assert_not_awaited()
