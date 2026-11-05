"""Offline ownership must not masquerade as a choice in a later period."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from tests.test_quiet_hours_runtime import (
    _config,
    _install_registry,
    _install_services,
    _seed_room,
    _stateful_public_manager,
)


@pytest.mark.parametrize("periods", [0, 1, 2])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("returned", [0.8, 0.6, 0.2, 0.1])
@pytest.mark.parametrize("observe_end", [False, True])
async def test_offline_return_respects_the_ownership_period(
    hass, monkeypatch, periods, restart, returned, observe_end
):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.8)
    manager._config = _config()
    writes, switches = await _install_services(hass)
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    await manager.async_reconcile(now=now)
    media = "media_player.bedroom"
    hass.states.async_set(media, "unavailable")
    for day in range(1, periods + 1):
        if observe_end:
            await manager.async_reconcile(now=now + timedelta(days=day, hours=-14))
        await manager.async_reconcile(now=now + timedelta(days=day))
        assert manager.active["controls"][media]["original_value"] == 0.8
    if restart:
        saved = deepcopy(manager._store.async_save.call_args.args[0])
        replacement = _stateful_public_manager(hass)
        replacement._config = runtime._config_from_data(saved["config"])
        replacement._active = replacement._normalize_active(saved["active"])
        manager = replacement
    writes.clear()
    switches.clear()
    hass.states.async_set(media, "idle", {"volume_level": returned})
    for _ in range(3):
        await manager.async_reconcile(now=now + timedelta(days=periods, hours=1))
    expected = min(0.2, returned) if periods else returned
    assert hass.states.get(media).attributes["volume_level"] == expected
    if not periods:
        assert writes == []  # Do not reclaim an independent same-period value.
    elif returned <= 0.2:
        assert writes == []  # Never restore loud audio just to reapply the ceiling.
        if returned == 0.2:
            saved = manager._store.async_save.call_args.args[0]["active"]
            assert not saved["controls"][media].get("restore_before_apply")
    elif returned > 0.2:
        assert writes == [(media, 0.2)]
        control = manager.active["controls"][media]
        assert control["original_value"] == returned
        persisted = manager._store.async_save.call_args.args[0]["active"]
        assert persisted["controls"][media]["original_value"] == returned
    await manager.async_reconcile(now=now + timedelta(days=periods + 1, hours=-14))
    assert hass.states.get(media).attributes["volume_level"] == (
        0.8 if returned == 0.2 else returned
    )


async def test_carry_forward_cannot_reclaim_a_later_manual_choice_after_restart(
    hass, monkeypatch
):
    from tests.test_quiet_hours_restoration_regressions import _active

    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.2)
    manager._config = _config(wake_sound="unchanged")
    media = "media_player.bedroom"
    manager._active = _active(media, "volume")
    control = manager._active["controls"][media]
    control.update(restoration_pending=True, restore_before_apply=True)
    writes, _ = await _install_services(hass)
    now = datetime(2026, 10, 3, 23, tzinfo=UTC)
    await manager.async_reconcile(now=now)
    saved = manager._store.async_save.call_args.args[0]["active"]
    manager._active = manager._normalize_active(deepcopy(saved))
    hass.states.async_set(media, "idle", {"volume_level": 0.6})
    for _ in range(3):
        await manager.async_reconcile(now=now)
    assert writes == []
    assert media not in manager.active["controls"]
    assert media in manager.active["observed_controls"]


@pytest.mark.parametrize("returned", [0.8, 0.6])
async def test_expired_unacknowledged_application_does_not_suppress_new_period(
    hass, monkeypatch, returned
):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.8)
    manager._config = _config(wake_sound="unchanged")
    writes, _ = await _install_services(hass)
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    await manager.async_reconcile(now=now)
    media = "media_player.bedroom"
    # A service effect may precede the durable acknowledgement of its intent.
    manager._active["controls"][media]["application_state"] = "prepared"
    hass.states.async_set(media, "unavailable")
    await manager.async_reconcile(now=now + timedelta(hours=10))
    await manager.async_reconcile(now=now + timedelta(days=1))
    manager._active = manager._normalize_active(deepcopy(manager.active))
    writes.clear()
    hass.states.async_set(media, "idle", {"volume_level": returned})
    hass.states.get(media).context = SimpleNamespace(id="returned-state")
    for _ in range(3):
        await manager.async_reconcile(now=now + timedelta(days=1, hours=1))
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    assert writes == [(media, 0.2)]
    assert manager.active["controls"][media]["original_value"] == returned
