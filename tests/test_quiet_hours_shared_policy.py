"""Core and public managers must enforce the same ownership transitions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours as public,
    quiet_hours_runtime as runtime,
)
from homeassistant.core import Context, State
from tests.test_quiet_hours_runtime import (
    _config,
    _install_registry,
    _install_services,
    _seed_room,
    _stateful_public_manager,
)
from tests.test_review_quiet_recovery import _control, timers  # noqa: F401


def _manager(hass, manager_type):
    manager = _stateful_public_manager(hass)
    if manager_type is runtime.QuietHoursManager:
        manager = manager_type(hass)
        manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
        manager._initialized = True
    return manager


@pytest.mark.parametrize(
    "manager_type", [runtime.QuietHoursManager, public.QuietHoursManager]
)
@pytest.mark.parametrize("ending", ["disable", "shutdown"])
async def test_shared_policy_preserves_baseline_through_retarget_and_disable(
    hass, monkeypatch, timers, manager_type, ending
):
    _install_registry(monkeypatch, "bedroom")
    manager = _manager(hass, manager_type)
    _seed_room(hass, "bedroom", volume=0.8)
    calls, switches = await _install_services(hass)
    manager._config = _config(enabled=True)
    generations = []
    manager._store.async_save.side_effect = lambda data: generations.append(
        deepcopy(data)
    )
    await manager.async_reconcile()
    prepared = [
        generation["active"]["controls"].get("media_player.bedroom", {})
        for generation in generations
        if generation["active"] is not None
    ]
    assert any(control.get("application_state") == "prepared" for control in prepared)
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.8
    await manager.async_update_config(_config(enabled=True, max_volume=0.1).as_dict())
    assert calls == [("media_player.bedroom", 0.2), ("media_player.bedroom", 0.1)]
    if ending == "disable":
        await manager.async_set_enabled(False)
    else:
        await manager.async_shutdown()
    assert calls[-1] == ("media_player.bedroom", 0.8)
    assert switches == [
        ("switch.bedroom_wake_sound", False),
        ("switch.bedroom_wake_sound", True),
    ]
    assert manager.active is None
    assert not manager._unsubscribers


@pytest.mark.parametrize(
    "manager_type", [runtime.QuietHoursManager, public.QuietHoursManager]
)
async def test_shared_policy_does_not_restore_unattributed_prepared_effect(
    hass, manager_type
):
    manager = _manager(hass, manager_type)
    entity = "media_player.speaker"
    hass.data["_quiet_hours_test_states"][entity] = State(
        entity, "idle", {"volume_level": 0.2}, context=Context(id="independent-effect")
    )
    control = _control("A", 0.8)
    control.pop("registry_identity")
    control.update(
        application_state="prepared", application_context_id="quiet-hours-effect"
    )
    manager._active = {"controls": {entity: control}, "observed_controls": [entity]}
    await manager._async_restore_locked()
    hass.services.async_call.assert_not_awaited()
    assert hass.states.get(entity).attributes["volume_level"] == 0.2
    assert manager.active is None


@pytest.mark.parametrize(
    "manager_type", [runtime.QuietHoursManager, public.QuietHoursManager]
)
@pytest.mark.parametrize("with_pending", [False, True])
async def test_shared_policy_rechecks_registry_identity_after_save(
    hass, monkeypatch, manager_type, with_pending
):
    manager = _manager(hass, manager_type)
    old, new, current = (f"media_player.{name}" for name in ("old", "new", "current"))
    lookup = {"A": new}
    monkeypatch.setattr(
        runtime.er,
        "async_get",
        lambda _h: SimpleNamespace(async_get_entity_id=lambda d, p, u: lookup.get(u)),
    )
    for entity in (old, new, current):
        hass.states.async_set(entity, "idle", {"volume_level": 0.2})
    calls, _ = await _install_services(hass)
    manager._active = {
        "controls": {old: _control("A", 0.8)},
        "observed_controls": [old],
    }

    if with_pending:
        lookup["B"] = "media_player.pending"
        lookup["S"] = "assist_satellite.pending"
        manager._active["pending_controls"] = {
            "media_player.pending": {
                **_control("B", 0.5),
                "satellite_entity_id": "assist_satellite.pending",
                "satellite_registry_identity": ["assist_satellite", "example", "S"],
            }
        }

    async def save(_data):
        lookup["A"] = current

    manager._store.async_save.side_effect = save
    await manager._async_restore_locked()
    assert calls == []
    assert manager.active["controls"][new]["restoration_pending"]
    await manager._async_restore_locked()
    assert calls == [(current, 0.8)]
    assert hass.states.get(old).attributes["volume_level"] == 0.2
    assert hass.states.get(new).attributes["volume_level"] == 0.2
    assert manager.active is None
