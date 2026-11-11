"""Public getters must retain unresolved device ownership across recovery."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    async_get_quiet_hours,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.parametrize("layout", ["controls", "pending_controls", "targets", "mixed"])
@pytest.mark.parametrize("version,minor_version", [(1, 1), (2, 0), (2, 1)])
async def test_legacy_ownership_keeps_native_store_bytes_unchanged(
    hass,
    monkeypatch,
    real_store_io,
    layout,
    version,
    minor_version,
):
    """The actual HA Store must never migrate or overwrite unverified baselines."""
    from unittest.mock import AsyncMock

    satellite, media, wake = _install_satellite_entities(hass, volume=0.2)
    calls = []
    _install_control_services(hass, calls)
    now = dt_util.now()
    active = {
        "period_started_at": (now - timedelta(hours=1)).isoformat(),
        "period_ends_at": (now + timedelta(hours=1)).isoformat(),
        "controls": {},
        "unknown_legacy_metadata": {"preserve": "exactly"},
    }
    record = {
        "kind": "volume",
        "satellite_entity_id": satellite,
        "original_value": 0.8,
        "quiet_value": 0.2,
    }
    if layout == "targets":
        active.pop("controls")
        active["targets"] = {media: {"original_volume": 0.8, "quiet_volume": 0.2}}
    elif layout == "mixed":
        entry = er.async_get(hass).async_get(media)
        active["controls"] = {
            media: {
                **record,
                "registry_identity": [entry.domain, entry.platform, entry.unique_id],
            },
            wake: {
                "kind": "switch",
                "satellite_entity_id": satellite,
                "original_value": True,
                "quiet_value": False,
            },
        }
    else:
        active[layout] = {media: record}
    generation = {
        "config": {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.2,
            "wake_sound": "off",
            "overrides": {satellite: {"media_player_entity_id": media}},
        },
        "active": active,
    }
    manager = QuietHoursManager(hass)
    path = Path(manager._store.path)
    envelope = {
        "version": version,
        "minor_version": minor_version,
        "key": f"{DOMAIN}.quiet_hours",
        "data": generation,
    }
    original = (json.dumps(envelope, indent=3) + "\n").encode()

    def write():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)

    await hass.async_add_executor_job(write)
    save = AsyncMock(wraps=manager._store.async_save)
    monkeypatch.setattr(manager._store, "async_save", save)
    await manager.async_setup()
    assert manager.snapshot()["available"] is False
    assert "verified registry identities" in manager.snapshot()["error"]
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager.async_set_enabled(False)
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager.async_update_config({"enabled": False})
    await manager.async_reconcile(now=now + timedelta(days=2))
    await manager.async_shutdown()
    save.assert_not_awaited()
    assert path.read_bytes() == original
    assert not calls
    replacement = QuietHoursManager(hass)
    await replacement.async_setup()
    await replacement.async_shutdown()
    assert replacement.startup_unavailable
    assert path.read_bytes() == original
    assert not calls


@pytest.mark.parametrize("ownership", ["observed_controls", "pending_controls"])
@pytest.mark.parametrize("missing", [None, {}, "config_only", "active_only"])
async def test_missing_readback_preserves_nonapplied_ownership(
    hass, monkeypatch, ownership, missing
):
    _, media, _ = _install_satellite_entities(
        hass, volume=0.1 if ownership == "observed_controls" else 0.8
    )
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
    if ownership == "observed_controls":
        await manager.async_update_config(config)
    save, load = manager._store.async_save, manager._store.async_load
    fail, readable = True, False
    committed = None

    async def failing_save(data):
        nonlocal committed
        await save(data)
        if fail and (
            ownership == "observed_controls"
            or (data.get("active") or {}).get("pending_controls")
        ):
            committed = deepcopy(data)
            raise OSError("committed but acknowledgement lost")

    async def missing_load():
        if readable:
            return await load()
        return (
            {"config": config}
            if missing == "config_only"
            else {"active": None}
            if missing == "active_only"
            else missing
        )

    monkeypatch.setattr(manager._store, "async_save", failing_save)
    monkeypatch.setattr(manager._store, "async_load", missing_load)
    try:
        with pytest.raises(OSError):
            await manager.async_update_config({**config, "max_volume": 0.25})
        assert committed is not None
        assert manager._config_recovery_pending
        assert manager.active[ownership]
        assert not manager.active["controls"]
        assert not calls
        assert len(manager._unsubscribers) == 1
        assert await load() == committed
        hass.states.async_set(
            media, "idle", {"volume_level": 0.7}, context=Context(user_id="manual")
        )
        fail, readable = False, True
        assert await async_get_quiet_hours(hass) is manager
        assert not manager._config_recovery_pending
        assert not calls
        assert hass.states.get(media).attributes["volume_level"] == 0.7
        durable = await load()
        assert not durable["active"]["controls"]
        assert not durable["active"]["pending_controls"]
        assert media in durable["active"]["observed_controls"]
        await manager.async_set_enabled(False)
        assert not calls
        assert hass.states.get(media).attributes["volume_level"] == 0.7
    finally:
        fail, readable = False, True
        await manager.async_shutdown()


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
