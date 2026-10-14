"""Public getters must retain unresolved device ownership across recovery."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.util import dt as dt_util
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("unavailable", [None, {}, "invalid_active"])
async def test_public_getter_preserves_unresolved_generation(
    hass, committed, unavailable
):
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    now = dt_util.now()
    config = {
        "enabled": True,
        "start": (now - timedelta(hours=1)).strftime("%H:%M"),
        "end": (now + timedelta(hours=1)).strftime("%H:%M"),
        "max_volume": 0.2,
        "wake_sound": "unchanged",
    }
    await manager.async_update_config(config)
    durable = {"config": manager.config.as_dict(), "active": deepcopy(manager.active)}
    authoritative = deepcopy(durable)
    readable = False
    fail = True

    async def save(data):
        nonlocal durable
        if committed or not fail:
            durable = deepcopy(data)
        if fail:
            raise OSError("write acknowledgement unavailable")

    async def load():
        if not readable:
            raise OSError("readback unavailable")
        return deepcopy(durable)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    try:
        with pytest.raises(OSError):
            await manager.async_set_enabled(False)
        confirmed = deepcopy(durable)
        readable = True
        durable = (
            {"config": config, "active": "broken"}
            if unavailable == "invalid_active"
            else unavailable
        )
        assert await async_get_quiet_hours(hass) is manager
        assert manager._config_recovery_pending
        assert manager.active["controls"][media]["original_value"] == 0.8
        assert len(manager._unsubscribers) == 1
        assert hass.states.get(media).attributes["volume_level"] == 0.2
        assert calls == [("volume_set", media)]
        # The confirmed generation becomes readable again; recovery, including
        # restoration for a committed disable, must precede return from getter.
        durable = confirmed if committed else authoritative
        fail = False
        assert await async_get_quiet_hours(hass) is manager
        assert not manager._config_recovery_pending
        if not committed:
            await manager.async_set_enabled(False)
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert durable["active"] is None
        assert manager.active is None
        assert not manager._unsubscribers
    finally:
        fail = False
        readable = True
        if not isinstance(durable, dict) or not isinstance(durable.get("active"), dict):
            durable = authoritative
        await manager.async_shutdown()


async def test_public_getter_repeated_cancellation_settles_recovery(hass):
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    _install_control_services(hass)
    manager = await async_get_quiet_hours(hass)
    now = dt_util.now()
    await manager.async_update_config(
        {
            "enabled": True,
            "start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "max_volume": 0.2,
            "wake_sound": "unchanged",
        }
    )
    durable = {"config": manager.config.as_dict(), "active": deepcopy(manager.active)}
    entered, finish = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await finish.wait()
        return deepcopy(durable)

    async def save(data):
        durable.update(deepcopy(data))

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    manager._invalidate_generation()
    task = asyncio.create_task(async_get_quiet_hours(hass))
    try:
        await entered.wait()
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert manager._lock.locked()
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not manager._config_recovery_pending
        assert manager.active["controls"][media]["original_value"] == 0.8
        assert len(manager._unsubscribers) == 3
        await manager.async_set_enabled(False)
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert durable["active"] is None
    finally:
        finish.set()
        await manager.async_shutdown()
