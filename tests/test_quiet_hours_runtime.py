"""Quiet Hours public and base runtime lifecycle, ownership, persistence and service contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours,
    quiet_hours_runtime as runtime,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    SERVICE_DISABLE_QUIET_HOURS,
    SERVICE_ENABLE_QUIET_HOURS,
    QuietHoursManager,
    _register_quiet_hours_actions,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursConfig,
    QuietHoursManager as RuntimeQuietHoursManager,
    QuietPeriod,
    SatelliteCapabilities,
    _config_from_data,
)
from homeassistant.exceptions import HomeAssistantError

# Public manager: stateful service integration.


def _config(**overrides):
    value = {
        "enabled": True,
        "start": "22:00",
        "end": "07:00",
        "max_volume": 0.2,
        "wake_sound": "off",
        "overrides": {},
    }
    value.update(overrides)
    return _config_from_data(value)


def _install_state_machine(hass) -> dict[str, SimpleNamespace]:
    """Give the lightweight repository hass fixture a stateful state machine."""
    states = hass.data.setdefault("_quiet_hours_test_states", {})

    def async_set(entity_id: str, state: str, attributes=None) -> None:
        states[entity_id] = SimpleNamespace(
            entity_id=entity_id,
            state=state,
            attributes=dict(attributes or {}),
        )

    def async_all(domain=None):
        values = list(states.values())
        if domain is None:
            return values
        domains = {domain} if isinstance(domain, str) else set(domain)
        return [
            state for state in values if state.entity_id.partition(".")[0] in domains
        ]

    def async_remove(entity_id: str) -> bool:
        return states.pop(entity_id, None) is not None

    hass.states.get.side_effect = states.get
    hass.states.async_set.side_effect = async_set
    hass.states.async_all.side_effect = async_all
    hass.states.async_remove.side_effect = async_remove
    hass.states.__getitem__.side_effect = states.__getitem__
    return states


def _stateful_public_manager(hass) -> QuietHoursManager:
    _install_state_machine(hass)
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    manager._registered_state_entity_id = "binary_sensor.extended_openai_quiet_hours"
    manager._unsubscribers = []
    return manager


def test_state_entity_falls_back_while_entity_registry_is_loading(
    monkeypatch, hass
) -> None:
    class LoadingRegistry:
        def async_get_or_create(self, **kwargs):
            return self.entities.get(kwargs["unique_id"])

    monkeypatch.setattr(runtime.er, "async_get", lambda _hass: LoadingRegistry())
    manager = QuietHoursManager(hass)

    assert manager._state_entity_id() == "binary_sensor.extended_openai_quiet_hours"
    assert manager._registered_state_entity_id == manager._state_entity_id()


def _room_entry(entity_id: str, domain: str, device_id: str, *, name: str):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=None,
        platform="esphome",
        original_name=name,
        name=None,
    )


def _install_registry(monkeypatch, *rooms: str) -> None:
    entries = {}
    for room in rooms:
        device_id = f"device-{room}"
        entries[f"assist_satellite.{room}"] = _room_entry(
            f"assist_satellite.{room}",
            "assist_satellite",
            device_id,
            name="Assist satellite",
        )
        entries[f"media_player.{room}"] = _room_entry(
            f"media_player.{room}", "media_player", device_id, name="Media Player"
        )
        entries[f"switch.{room}_wake_sound"] = _room_entry(
            f"switch.{room}_wake_sound", "switch", device_id, name="Wake sound"
        )
    registry = SimpleNamespace(async_get=entries.get)
    monkeypatch.setattr(runtime.er, "async_get", lambda _hass: registry)


def _seed_room(hass, room: str, *, volume: float = 0.55, wake: str = "on") -> None:
    _install_state_machine(hass)
    hass.states.async_set(
        f"assist_satellite.{room}", "idle", {"friendly_name": f"{room.title()} Voice"}
    )
    hass.states.async_set(f"media_player.{room}", "idle", {"volume_level": volume})
    hass.states.async_set(f"switch.{room}_wake_sound", wake)


async def _install_services(hass):
    volume_calls: list[tuple[str, float]] = []
    switch_calls: list[tuple[str, bool]] = []

    async def async_call(domain, service, data, *, blocking=False) -> None:
        entity_id = data["entity_id"]
        if domain == "media_player" and service == "volume_set":
            volume = float(data["volume_level"])
            volume_calls.append((entity_id, volume))
            current = hass.states.get(entity_id)
            attrs = dict(current.attributes) if current else {}
            attrs["volume_level"] = volume
            hass.states.async_set(
                entity_id, current.state if current else "idle", attrs
            )
            return
        if domain == "switch" and service in {"turn_on", "turn_off"}:
            enabled = service == "turn_on"
            switch_calls.append((entity_id, enabled))
            hass.states.async_set(entity_id, "on" if enabled else "off")
            return
        raise AssertionError(f"Unexpected service call: {domain}.{service}")

    hass.services.async_call = AsyncMock(side_effect=async_call)
    return volume_calls, switch_calls


@pytest.mark.asyncio
async def test_ceiling_only_lowers_louder_satellites(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom", "kitchen")
    _seed_room(hass, "bedroom", volume=0.55)
    _seed_room(hass, "kitchen", volume=0.10)
    manager = _stateful_public_manager(hass)
    manager._config = _config(wake_sound="unchanged")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == []
    assert hass.states["media_player.kitchen"].attributes["volume_level"] == 0.10
    assert "media_player.bedroom" in manager.active["controls"]
    assert "media_player.kitchen" not in manager.active["controls"]
    assert "media_player.kitchen" in manager.active["observed_controls"]


@pytest.mark.asyncio
async def test_schedule_entity_turns_on_even_when_policy_is_noop(
    monkeypatch, hass
) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.10, wake="off")
    manager = _stateful_public_manager(hass)
    manager._config = _config()
    await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    state = hass.states["binary_sensor.extended_openai_quiet_hours"]
    assert state.state == "on"
    assert state.attributes["max_volume"] == 0.2
    assert state.attributes["wake_sound"] == "off"
    assert manager.active["controls"] == {}

    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))
    assert hass.states["binary_sensor.extended_openai_quiet_hours"].state == "off"


@pytest.mark.asyncio
async def test_ownership_is_saved_before_mutating_controls(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _stateful_public_manager(hass)
    manager._config = _config()
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55
    assert (
        manager.active["controls"]["switch.bedroom_wake_sound"]["original_value"]
        is True
    )
    saves = manager._store.async_save.await_args_list
    assert any(
        "media_player.bedroom" in call.args[0].get("active", {}).get("controls", {})
        for call in saves
    )


@pytest.mark.asyncio
async def test_restart_same_period_preserves_originals_without_reapplying(
    monkeypatch, hass
) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.2, wake="off")
    manager = _stateful_public_manager(hass)
    manager._config = _config()
    manager._active = {
        "period_started_at": "2026-09-11T22:00:00+00:00",
        "period_ends_at": "2026-09-12T07:00:00+00:00",
        "applied_at": "2026-09-11T22:00:00+00:00",
        "controls": {
            "media_player.bedroom": {
                "kind": "volume",
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": 0.55,
                "quiet_value": 0.2,
            },
            "switch.bedroom_wake_sound": {
                "kind": "switch",
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": True,
                "quiet_value": False,
            },
        },
        "observed_controls": [
            "media_player.bedroom",
            "switch.bedroom_wake_sound",
        ],
    }
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))

    assert volume_calls == []
    assert switch_calls == []
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55


@pytest.mark.asyncio
async def test_manual_changes_are_not_reclaimed_or_restored(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _stateful_public_manager(hass)
    manager._config = _config()
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})
    hass.states.async_set("switch.bedroom_wake_sound", "on")

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35
    assert hass.states["switch.bedroom_wake_sound"].state == "on"
    assert manager.active is None


@pytest.mark.asyncio
async def test_end_restores_controls_that_are_still_owned(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _stateful_public_manager(hass)
    manager._config = _config()
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.bedroom", 0.55),
    ]
    assert switch_calls == [
        ("switch.bedroom_wake_sound", False),
        ("switch.bedroom_wake_sound", True),
    ]
    assert manager.active is None


@pytest.mark.asyncio
async def test_periodic_discovery_adds_new_satellite_but_not_manual_change(
    monkeypatch, hass
) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _stateful_public_manager(hass)
    manager._config = _config(wake_sound="unchanged")
    volume_calls, _ = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})

    _install_registry(monkeypatch, "bedroom", "kitchen")
    _seed_room(hass, "kitchen", volume=0.6)
    await manager.async_reconcile(now=datetime(2026, 9, 11, 23, 0, tzinfo=UTC))

    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.kitchen", 0.2),
    ]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35


@pytest.mark.asyncio
async def test_failed_mutation_releases_ownership_for_retry(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _stateful_public_manager(hass)
    manager._config = _config(wake_sound="unchanged")
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("boom"))

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert "media_player.bedroom" not in manager.active["controls"]
    assert "media_player.bedroom" not in manager.active["observed_controls"]


@pytest.mark.asyncio
async def test_async_set_enabled_preserves_policy(monkeypatch, hass) -> None:
    manager = _stateful_public_manager(hass)
    manager._config = _config(
        enabled=False,
        start="21:30",
        end="06:15",
        max_volume=0.17,
        wake_sound="unchanged",
    )
    manager.async_update_config = AsyncMock(return_value={"active": False})

    result = await manager.async_set_enabled(True)

    assert result == {"active": False}
    submitted = manager.async_update_config.await_args.args[0]
    assert submitted["enabled"] is True
    assert submitted["start"] == "21:30"
    assert submitted["end"] == "06:15"
    assert submitted["max_volume"] == 0.17
    assert submitted["wake_sound"] == "unchanged"
    with pytest.raises(ValueError, match="boolean"):
        await manager.async_set_enabled("yes")


@pytest.mark.asyncio
async def test_enable_disable_actions_are_global_and_idempotently_registered(
    hass,
) -> None:
    manager = _stateful_public_manager(hass)
    manager._config = _config(enabled=False)
    manager.async_set_enabled = AsyncMock()
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = manager

    registered = {}
    hass.services.has_service.side_effect = lambda domain, service: (
        (
            domain,
            service,
        )
        in registered
    )
    hass.services.async_register.side_effect = lambda domain, service, handler: (
        registered.__setitem__((domain, service), handler)
    )

    async def async_call(domain, service, data, *, blocking=False):
        handler = registered[(domain, service)]
        await handler(SimpleNamespace(data=data, context=SimpleNamespace(user_id=None)))

    hass.services.async_call = AsyncMock(side_effect=async_call)

    _register_quiet_hours_actions(hass)
    _register_quiet_hours_actions(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_ENABLE_QUIET_HOURS)
    assert hass.services.has_service(DOMAIN, SERVICE_DISABLE_QUIET_HOURS)

    await hass.services.async_call(
        DOMAIN, SERVICE_ENABLE_QUIET_HOURS, {}, blocking=True
    )
    manager.async_set_enabled.assert_awaited_once_with(True)

    await hass.services.async_call(
        DOMAIN, SERVICE_DISABLE_QUIET_HOURS, {}, blocking=True
    )
    manager.async_set_enabled.assert_awaited_with(False)


@pytest.mark.asyncio
async def test_shutdown_unsubscribes_and_removes_state(hass) -> None:
    manager = _stateful_public_manager(hass)
    unsub = MagicMock()
    manager._unsubscribers = [unsub]
    hass.states.async_set("binary_sensor.extended_openai_quiet_hours", "off")

    await manager.async_shutdown()

    unsub.assert_called_once_with()
    assert hass.states.get("binary_sensor.extended_openai_quiet_hours") is None


# Public manager: discovery, registry, authorization and ownership boundaries.


def _public_state(entity_id: str, state: str = "idle", **attributes: Any) -> Any:
    return SimpleNamespace(entity_id=entity_id, state=state, attributes=attributes)


def _registry_entry(
    entity_id: str,
    domain: str,
    device_id: str | None = None,
    *,
    disabled_by: object | None = None,
    name: str | None = None,
    original_name: str | None = None,
) -> Any:
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=disabled_by,
        platform="esphome",
        name=name,
        original_name=original_name,
    )


def _public_manager(
    hass: Any, config: QuietHoursConfig | None = None
) -> QuietHoursManager:
    manager = object.__new__(QuietHoursManager)
    manager.hass = hass
    manager._config = config or QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = True
    manager._unsubscribers = []
    manager._registered_state_entity_id = None
    return manager


def test_discovery_filters_disabled_registryless_and_unrelated_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = [
        _public_state("assist_satellite.disabled"),
        _public_state("assist_satellite.named"),
        _public_state("assist_satellite.registryless"),
        _public_state("media_player.enabled", volume_level=0.5),
        _public_state("media_player.disabled", volume_level=0.5),
        _public_state("media_player.registryless", volume_level=0.5),
        _public_state("switch.enabled", state="on"),
        _public_state("light.unrelated", state="on"),
    ]
    entries = {
        "assist_satellite.disabled": _registry_entry(
            "assist_satellite.disabled",
            "assist_satellite",
            "disabled-device",
            disabled_by="user",
        ),
        "assist_satellite.named": _registry_entry(
            "assist_satellite.named",
            "assist_satellite",
            "device-1",
            name="Registry Name",
        ),
        "media_player.enabled": _registry_entry(
            "media_player.enabled", "media_player", "device-1"
        ),
        "media_player.disabled": _registry_entry(
            "media_player.disabled",
            "media_player",
            "device-1",
            disabled_by="user",
        ),
        "switch.enabled": _registry_entry(
            "switch.enabled",
            "switch",
            "device-1",
            original_name="Wake sound",
        ),
    }
    registry = SimpleNamespace(async_get=entries.get)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            async_all=lambda: states,
            get=lambda entity_id: next(
                (item for item in states if item.entity_id == entity_id), None
            ),
        )
    )
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _hass: registry)

    discovered = quiet_hours.discover_satellite_capabilities(hass, QuietHoursConfig())

    assert [item.satellite_entity_id for item in discovered] == [
        "assist_satellite.named",
        "assist_satellite.registryless",
    ]
    assert discovered[0].name == "Registry Name"
    assert discovered[0].media_player_entity_id == "media_player.enabled"
    assert discovered[0].wake_sound_entity_id == "switch.enabled"
    assert discovered[1].name == "assist_satellite.registryless"
    assert discovered[1].media_player_entity_id is None
    assert discovered[1].wake_sound_entity_id is None


@pytest.mark.parametrize("mode", ["missing-create", "registry-unready", "bad-id"])
def test_state_entity_id_uses_fallback_when_registration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    if mode == "missing-create":
        registry = SimpleNamespace()
    elif mode == "registry-unready":

        class Registry:
            def async_get_or_create(self, **_kwargs: Any) -> Any:
                return self.entities["quiet_hours"]

        registry = Registry()
    else:
        registry = SimpleNamespace(
            async_get_or_create=lambda **_kwargs: SimpleNamespace(entity_id=None)
        )
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _hass: registry)
    manager = _public_manager(SimpleNamespace())

    assert manager._state_entity_id() == quiet_hours._STATE_FALLBACK_ENTITY_ID
    assert manager._state_entity_id() == quiet_hours._STATE_FALLBACK_ENTITY_ID


def test_state_entity_id_registers_once_and_propagates_other_attribute_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = MagicMock(return_value=SimpleNamespace(entity_id="binary_sensor.quiet"))
    registry = SimpleNamespace(async_get_or_create=create)
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _hass: registry)
    manager = _public_manager(SimpleNamespace())

    assert manager._state_entity_id() == "binary_sensor.quiet"
    assert manager._state_entity_id() == "binary_sensor.quiet"
    create.assert_called_once()

    def fail(**_kwargs: Any) -> Any:
        raise AttributeError("other", name="other")

    registry.async_get_or_create = fail
    manager._registered_state_entity_id = None
    with pytest.raises(AttributeError, match="other"):
        manager._state_entity_id()


def test_publish_state_projects_active_and_inactive_periods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quiet_hours.er,
        "async_get",
        lambda _hass: SimpleNamespace(
            async_get_or_create=lambda **_kwargs: SimpleNamespace(
                entity_id="binary_sensor.quiet"
            )
        ),
    )
    states = SimpleNamespace(async_set=MagicMock())
    manager = _public_manager(
        SimpleNamespace(states=states),
        QuietHoursConfig(enabled=True, max_volume=0.2, wake_sound="off"),
    )
    period = QuietPeriod(
        datetime(2026, 9, 14, 22, tzinfo=UTC),
        datetime(2026, 9, 15, 7, tzinfo=UTC),
    )

    manager._publish_state(period)
    manager._publish_state(None)

    first = states.async_set.call_args_list[0]
    assert first.args[0:2] == ("binary_sensor.quiet", "on")
    assert first.args[2]["period_started_at"] == period.start.isoformat()
    assert first.args[2]["period_ends_at"] == period.end.isoformat()
    assert states.async_set.call_args_list[1].args[1] == "off"


async def test_set_enabled_validates_and_delegates() -> None:
    manager = _public_manager(SimpleNamespace(), QuietHoursConfig(enabled=False))
    manager.async_update_config = AsyncMock(return_value={"enabled": True})

    with pytest.raises(ValueError, match="boolean"):
        await manager.async_set_enabled(1)  # type: ignore[arg-type]

    assert await manager.async_set_enabled(True) == {"enabled": True}
    assert manager.async_update_config.await_args.args[0]["enabled"] is True


async def test_reconcile_handles_uninitialized_inactive_and_period_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    manager = _public_manager(SimpleNamespace())
    manager._publish_state = MagicMock()
    manager._async_restore_locked = AsyncMock()
    manager._initialized = False

    await manager.async_reconcile(now=now)
    manager._publish_state.assert_not_called()

    manager._initialized = True
    manager._config = QuietHoursConfig(enabled=False)
    await manager.async_reconcile(now=now)
    manager._publish_state.assert_called_once_with(None)
    manager._async_restore_locked.assert_awaited_once_with()

    manager._publish_state.reset_mock()
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    manager._config = QuietHoursConfig(enabled=True, max_volume=0.2, wake_sound="off")
    manager._active = {
        "period_started_at": "old-period",
        "controls": {},
        "observed_controls": [],
    }

    async def restore() -> None:
        manager._active = None

    manager._async_restore_locked = AsyncMock(side_effect=restore)
    manager._async_save_locked = AsyncMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    monkeypatch.setattr(quiet_hours, "quiet_period_for", lambda *_args: period)
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_args: [
            SatelliteCapabilities(
                satellite_entity_id="assist_satellite.kitchen",
                name="Kitchen",
                device_id="device",
                media_player_entity_id="media_player.kitchen",
                wake_sound_entity_id="switch.kitchen_wake",
                media_player_source="auto",
                wake_sound_source="auto",
            )
        ],
    )

    await manager.async_reconcile(now=now)

    manager._async_restore_locked.assert_awaited_once_with()
    assert manager._active is not None
    assert manager._active["period_started_at"] == period.start.isoformat()
    manager._async_save_locked.assert_awaited_once_with()
    manager._async_apply_volume_locked.assert_awaited_once()
    manager._async_apply_switch_locked.assert_awaited_once_with(
        "assist_satellite.kitchen",
        "switch.kitchen_wake",
        False,
        {},
    )


async def test_volume_apply_covers_skip_noop_success_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _public_manager(SimpleNamespace(), QuietHoursConfig(max_volume=0.2))
    manager._active = {
        "controls": {"media_player.owned": {"kind": "volume"}},
        "observed_controls": ["media_player.observed"],
    }
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    controls = manager._active["controls"]

    for entity_id in ("media_player.owned", "media_player.observed"):
        await manager._async_apply_volume_locked(
            "assist_satellite.test", entity_id, controls
        )
    manager._async_save_locked.assert_not_awaited()

    monkeypatch.setattr(quiet_hours, "_current_volume", lambda *_args: None)
    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.unknown", controls
    )
    manager._async_save_locked.assert_not_awaited()

    values = iter((0.1, 0.8, 0.9))
    monkeypatch.setattr(quiet_hours, "_current_volume", lambda *_args: next(values))
    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.quiet", controls
    )
    assert "media_player.quiet" in manager._active["observed_controls"]

    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.loud", controls
    )
    assert controls["media_player.loud"]["original_value"] == 0.8
    manager._async_set_volume.assert_awaited_with("media_player.loud", 0.2)

    manager._async_set_volume.side_effect = RuntimeError("service failed")
    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.failed", controls
    )
    assert "media_player.failed" not in controls
    assert "media_player.failed" not in manager._active["observed_controls"]


async def test_switch_apply_covers_skip_noop_success_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _public_manager(SimpleNamespace(), QuietHoursConfig(wake_sound="off"))
    manager._active = {
        "controls": {"switch.owned": {"kind": "switch"}},
        "observed_controls": ["switch.observed"],
    }
    manager._async_save_locked = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = manager._active["controls"]

    for entity_id in ("switch.owned", "switch.observed"):
        await manager._async_apply_switch_locked(
            "assist_satellite.test", entity_id, False, controls
        )
    manager._async_save_locked.assert_not_awaited()

    monkeypatch.setattr(quiet_hours, "_current_switch", lambda *_args: None)
    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.unknown", False, controls
    )
    manager._async_save_locked.assert_not_awaited()

    values = iter((False, True, True))
    monkeypatch.setattr(quiet_hours, "_current_switch", lambda *_args: next(values))
    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.already_off", False, controls
    )
    assert "switch.already_off" in manager._active["observed_controls"]

    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.on", False, controls
    )
    assert controls["switch.on"]["original_value"] is True
    manager._async_set_switch.assert_awaited_with("switch.on", False)

    manager._async_set_switch.side_effect = RuntimeError("service failed")
    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.failed", False, controls
    )
    assert "switch.failed" not in controls
    assert "switch.failed" not in manager._active["observed_controls"]


def test_normalize_active_merges_observed_and_owned_controls() -> None:
    manager = _public_manager(SimpleNamespace())
    assert manager._normalize_active(None) is None
    value = {
        "period_started_at": "2026-09-14T22:00:00+00:00",
        "period_ends_at": "2026-09-15T07:00:00+00:00",
        "controls": {
            "media_player.kitchen": {
                "kind": "volume",
                "satellite_entity_id": "assist_satellite.kitchen",
                "original_value": 0.7,
                "quiet_value": 0.2,
            }
        },
        "observed_controls": ["switch.wake", 123, "switch.wake"],
    }

    normalized = manager._normalize_active(value)

    assert normalized is not None
    assert normalized["observed_controls"] == [
        "media_player.kitchen",
        "switch.wake",
    ]


async def test_shutdown_unsubscribes_and_removes_registered_state() -> None:
    first = MagicMock()
    second = MagicMock()
    states = SimpleNamespace(async_remove=MagicMock())
    manager = _public_manager(SimpleNamespace(states=states))
    manager._registered_state_entity_id = "binary_sensor.quiet"
    manager._unsubscribers = [first, second]

    await manager.async_shutdown()

    first.assert_called_once_with()
    second.assert_called_once_with()
    assert manager._unsubscribers == []
    states.async_remove.assert_called_once_with("binary_sensor.quiet")


async def test_require_admin_allows_system_and_admin_and_rejects_restricted() -> None:
    auth = SimpleNamespace(async_get_user=AsyncMock())
    hass = SimpleNamespace(auth=auth)

    await quiet_hours._async_require_admin(
        hass, SimpleNamespace(context=SimpleNamespace(user_id=None))
    )
    auth.async_get_user.assert_not_awaited()

    auth.async_get_user.return_value = SimpleNamespace(is_admin=True)
    await quiet_hours._async_require_admin(
        hass, SimpleNamespace(context=SimpleNamespace(user_id="admin"))
    )

    for user in (None, SimpleNamespace(is_admin=False)):
        auth.async_get_user.return_value = user
        with pytest.raises(HomeAssistantError, match="Administrator permission"):
            await quiet_hours._async_require_admin(
                hass,
                SimpleNamespace(context=SimpleNamespace(user_id="restricted")),
            )


class _Services:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def has_service(self, _domain: str, service: str) -> bool:
        return service in self.registered

    def async_register(self, _domain: str, service: str, handler: Any) -> None:
        self.registered[service] = handler


async def test_service_registration_is_idempotent_and_handlers_toggle_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    hass = SimpleNamespace(
        services=services,
        auth=SimpleNamespace(async_get_user=AsyncMock()),
    )
    manager = SimpleNamespace(async_set_enabled=AsyncMock())
    monkeypatch.setattr(
        quiet_hours,
        "async_get_quiet_hours",
        AsyncMock(return_value=manager),
    )

    quiet_hours._register_quiet_hours_actions(hass)
    quiet_hours._register_quiet_hours_actions(hass)
    assert set(services.registered) == {
        SERVICE_ENABLE_QUIET_HOURS,
        SERVICE_DISABLE_QUIET_HOURS,
    }

    call = SimpleNamespace(context=SimpleNamespace(user_id=None))
    await services.registered[SERVICE_ENABLE_QUIET_HOURS](call)
    await services.registered[SERVICE_DISABLE_QUIET_HOURS](call)
    assert [item.args for item in manager.async_set_enabled.await_args_list] == [
        (True,),
        (False,),
    ]


async def test_async_get_quiet_hours_reuses_manager_and_replaces_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeManager:
        def __init__(self, hass: Any) -> None:
            self.hass = hass
            self.async_setup = AsyncMock()

    monkeypatch.setattr(quiet_hours, "QuietHoursManager", FakeManager)
    register = MagicMock()
    monkeypatch.setattr(quiet_hours, "_register_quiet_hours_actions", register)
    hass = SimpleNamespace(data={DOMAIN: {}}, services=SimpleNamespace())

    first = await quiet_hours.async_get_quiet_hours(hass)
    second = await quiet_hours.async_get_quiet_hours(hass)
    assert second is first
    assert first.async_setup.await_count == 2

    hass.data[DOMAIN][quiet_hours._RUNTIME_KEY] = object()
    replacement = await quiet_hours.async_get_quiet_hours(hass)
    assert isinstance(replacement, FakeManager)
    assert replacement is not first
    register.assert_called_with(hass)


# Base runtime: persistence, lifecycle and control-state recovery.
# Keep these cases separate from the public subclass overrides above.


class _States:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.removed: list[str] = []

    def get(self, entity_id: str):
        return self.values.get(entity_id)

    def async_all(self, domain=None):
        return [
            state
            for entity_id, state in self.values.items()
            if domain is None or entity_id.startswith(f"{domain}.")
        ]

    def async_set(self, entity_id: str, state: str, attributes=None) -> None:
        self.values[entity_id] = SimpleNamespace(
            entity_id=entity_id,
            state=state,
            attributes=dict(attributes or {}),
        )

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.values.pop(entity_id, None)


def _state(entity_id: str, state: str, **attributes):
    return SimpleNamespace(
        entity_id=entity_id,
        state=state,
        attributes=attributes,
    )


def _hass(states=None):
    return SimpleNamespace(
        data={},
        states=_States(states),
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def _runtime_manager(hass=None, *, initialized=True):
    manager = runtime.QuietHoursManager.__new__(runtime.QuietHoursManager)
    manager.hass = hass or _hass()
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={}),
        async_save=AsyncMock(),
    )
    manager._config = runtime.QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = initialized
    manager._unsubscribers = []
    return manager


def test_live_state_readers_and_picker_fallbacks() -> None:
    hass = _hass(
        {
            "media_player.good": _state("media_player.good", "idle", volume_level=0.4),
            "media_player.bool": _state("media_player.bool", "idle", volume_level=True),
            "media_player.range": _state(
                "media_player.range", "idle", volume_level=2.0
            ),
            "switch.on": _state("switch.on", "on"),
            "switch.unknown": _state("switch.unknown", "unknown"),
        }
    )
    assert runtime._safe_iso(None) is None
    assert runtime._safe_iso("2026-09-13T22:00:00") is None
    assert runtime._safe_iso("2026-09-13T22:00:00+00:00") is not None
    assert runtime._current_volume(hass, "media_player.good") == 0.4
    assert runtime._current_volume(hass, "media_player.bool") is None
    assert runtime._current_volume(hass, "media_player.range") is None
    assert runtime._current_volume(hass, "media_player.missing") is None
    assert runtime._current_switch(hass, "switch.on") is True
    assert runtime._current_switch(hass, "switch.unknown") is None

    def entry(entity_id, domain, **kwargs):
        return SimpleNamespace(
            entity_id=entity_id,
            domain=domain,
            disabled_by=kwargs.get("disabled_by"),
            platform=kwargs.get("platform", "other"),
            name=kwargs.get("name"),
            original_name=kwargs.get("name"),
        )

    generic = entry("media_player.good", "media_player")
    speaker = entry(
        "media_player.speaker",
        "media_player",
        platform="esphome",
        name="Bedroom Speaker",
    )
    disabled = entry(
        "media_player.disabled",
        "media_player",
        disabled_by="user",
    )
    assert runtime._pick_media_player(hass, [generic, speaker]) == "media_player.good"
    assert runtime._pick_media_player(hass, [disabled]) is None
    assert (
        runtime._pick_wake_sound([entry("switch.other", "switch", name="Other")])
        is None
    )


def test_discovery_skips_disabled_and_handles_unregistered_satellite(
    monkeypatch,
) -> None:
    hass = _hass(
        {
            "assist_satellite.disabled": _state("assist_satellite.disabled", "idle"),
            "assist_satellite.unregistered": _state(
                "assist_satellite.unregistered",
                "idle",
                friendly_name="Unregistered",
            ),
        }
    )
    disabled = SimpleNamespace(
        entity_id="assist_satellite.disabled",
        disabled_by="user",
        device_id="device-1",
        name=None,
        original_name=None,
    )
    registry = SimpleNamespace(
        async_get=lambda entity_id: (
            disabled if entity_id == "assist_satellite.disabled" else None
        )
    )
    monkeypatch.setattr(runtime.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        runtime.er,
        "async_entries_for_device",
        lambda *_args: pytest.fail("no device lookup expected"),
    )

    discovered = runtime.discover_satellite_capabilities(
        hass, runtime.QuietHoursConfig()
    )
    assert len(discovered) == 1
    assert discovered[0].name == "Unregistered"
    assert discovered[0].device_id is None
    assert discovered[0].media_player_entity_id is None


async def test_setup_recovers_invalid_storage_and_is_idempotent() -> None:
    manager = _runtime_manager(initialized=False)
    manager._store.async_load.return_value = {
        "config": {"max_volume": 2},
        "active": {"not": "valid"},
    }
    manager._reschedule = Mock()
    manager.async_reconcile = AsyncMock()

    await manager.async_setup()
    assert manager.config == runtime.QuietHoursConfig()
    assert manager.active is None
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()

    await manager.async_setup()
    manager._reschedule.assert_called_once_with()


async def test_update_config_rolls_back_then_succeeds() -> None:
    manager = _runtime_manager()
    previous = manager.config
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock(side_effect=RuntimeError("disk failed"))
    manager._reschedule = Mock()

    with pytest.raises(RuntimeError, match="disk failed"):
        await manager.async_update_config({"enabled": True, "max_volume": 0.1})
    assert manager.config == previous
    manager._reschedule.assert_not_called()

    manager._async_save_locked = AsyncMock()
    manager.async_reconcile = AsyncMock()
    manager.snapshot = Mock(return_value={"updated": True})
    result = await manager.async_update_config(
        {"enabled": True, "start": "21:00", "end": "06:00"}
    )
    assert result == {"updated": True}
    assert manager.config.enabled is True
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()


async def test_reconcile_covers_disabled_new_and_stale_periods(monkeypatch) -> None:
    manager = _runtime_manager(initialized=False)
    manager._publish_state = Mock()
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    manager._publish_state.assert_not_called()

    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=False)
    manager._async_restore_locked = AsyncMock()
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    manager._publish_state.assert_called_once_with(None)
    manager._async_restore_locked.assert_awaited_once_with()

    manager._config = runtime.QuietHoursConfig(
        enabled=True,
        start="22:00",
        end="07:00",
        wake_sound="off",
    )
    manager._active = None
    manager._async_restore_locked.reset_mock()
    manager._async_save_locked = AsyncMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    capability = runtime.SatelliteCapabilities(
        "assist_satellite.bedroom",
        "Bedroom",
        "device-1",
        "media_player.bedroom",
        "switch.bedroom_wake",
        "auto",
        "auto",
    )
    monkeypatch.setattr(
        runtime, "discover_satellite_capabilities", lambda *_args: [capability]
    )
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    assert manager._active is not None
    manager._async_apply_volume_locked.assert_awaited_once()
    manager._async_apply_switch_locked.assert_awaited_once()

    old = {
        "period_started_at": "2026-09-12T22:00:00+00:00",
        "controls": {},
    }
    manager._active = old

    async def restore():
        manager._active = None

    manager._async_restore_locked = AsyncMock(side_effect=restore)
    monkeypatch.setattr(runtime, "discover_satellite_capabilities", lambda *_args: [])
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    manager._async_restore_locked.assert_awaited_once_with()
    assert manager._active is not None
    assert manager._active["period_started_at"].startswith("2026-09-13T22:00")


async def test_apply_controls_claim_only_needed_values_and_release_on_failure() -> None:
    hass = _hass(
        {
            "media_player.loud": _state("media_player.loud", "idle", volume_level=0.8),
            "media_player.quiet": _state(
                "media_player.quiet", "idle", volume_level=0.1
            ),
            "switch.on": _state("switch.on", "on"),
            "switch.off": _state("switch.off", "off"),
        }
    )
    manager = _runtime_manager(hass)
    manager._config = runtime.QuietHoursConfig(max_volume=0.2)
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = {}

    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.quiet", controls
    )
    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.off", False, controls
    )
    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.on", False, controls
    )
    assert set(controls) == {"media_player.loud", "switch.on"}
    manager._async_set_volume.assert_awaited_once_with("media_player.loud", 0.2)
    manager._async_set_switch.assert_awaited_once_with("switch.on", False)

    controls.clear()
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    assert controls == {}


async def test_restore_respects_manual_changes_and_clears_failed_ownership() -> None:
    hass = _hass(
        {
            "media_player.owned": _state(
                "media_player.owned", "idle", volume_level=0.2
            ),
            "media_player.changed": _state(
                "media_player.changed", "idle", volume_level=0.4
            ),
            "switch.owned": _state("switch.owned", "off"),
            "switch.changed": _state("switch.changed", "off"),
        }
    )
    manager = _runtime_manager(hass)
    manager._active = {
        "controls": {
            "media_player.owned": {
                "kind": "volume",
                "original_value": 0.7,
                "quiet_value": 0.2,
            },
            "media_player.changed": {
                "kind": "volume",
                "original_value": 0.8,
                "quiet_value": 0.2,
            },
            "switch.owned": {
                "kind": "switch",
                "original_value": True,
                "quiet_value": False,
            },
            "switch.changed": {
                "kind": "switch",
                "original_value": False,
                "quiet_value": True,
            },
            123: {"kind": "switch"},
            "bad": "not-a-control",
        }
    }
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()
    manager._async_set_volume.assert_awaited_once_with("media_player.owned", 0.7)
    manager._async_set_switch.assert_awaited_once_with("switch.owned", True)
    assert manager._active is None

    manager._active = {
        "controls": {
            "media_player.owned": {
                "kind": "volume",
                "original_value": 0.7,
                "quiet_value": 0.2,
            }
        }
    }
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    await manager._async_restore_locked()
    assert manager._active is None


async def test_service_save_shutdown_and_callbacks() -> None:
    manager = _runtime_manager()
    manager._config = runtime.QuietHoursConfig(enabled=True, max_volume=0.15)
    manager._active = {"controls": {}}
    await manager._async_save_locked()
    manager._store.async_save.assert_awaited_once()

    await manager._async_set_volume("media_player.bedroom", 0.2)
    await manager._async_set_switch("switch.wake", True)
    await manager._async_set_switch("switch.wake", False)
    assert manager.hass.services.async_call.await_args_list == [
        call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.bedroom", "volume_level": 0.2},
            blocking=True,
        ),
        call("switch", "turn_on", {"entity_id": "switch.wake"}, blocking=True),
        call("switch", "turn_off", {"entity_id": "switch.wake"}, blocking=True),
    ]

    manager.async_reconcile = AsyncMock()
    now = datetime(2026, 9, 13, 22, tzinfo=UTC)
    await manager._handle_time_transition(now)
    await manager._handle_discovery_tick(now)
    assert manager.async_reconcile.await_args_list == [call(now=now), call(now=now)]

    first = Mock()
    second = Mock()
    manager._unsubscribers = [first, second]
    await manager.async_shutdown()
    first.assert_called_once_with()
    second.assert_called_once_with()
    assert runtime._STATE_ENTITY_ID in manager.hass.states.removed


def test_normalize_active_migrates_legacy_targets_and_filters_invalid() -> None:
    manager = _runtime_manager()
    assert manager._normalize_active(None) is None
    assert (
        manager._normalize_active(
            {
                "period_started_at": "bad",
                "period_ends_at": "also-bad",
                "controls": {},
            }
        )
        is None
    )
    assert (
        manager._normalize_active(
            {
                "period_started_at": "2026-09-13T22:00:00+00:00",
                "period_ends_at": "2026-09-13T21:00:00+00:00",
                "controls": {},
            }
        )
        is None
    )

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "targets": {
                "media_player.bedroom": {
                    "original_volume": 0.8,
                    "quiet_volume": 0.2,
                },
                "media_player.bad": "bad",
            },
        }
    )
    assert active is not None
    assert active["controls"]["media_player.bedroom"]["original_value"] == 0.8

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "controls": {
                "switch.wake": {
                    "kind": "switch",
                    "original_value": True,
                    "quiet_value": False,
                },
                "media_player.bad": {
                    "kind": "volume",
                    "original_value": 2,
                    "quiet_value": 0.2,
                },
            },
        }
    )
    assert active is not None
    assert set(active["controls"]) == {"switch.wake"}


def test_publish_snapshot_and_reschedule(monkeypatch) -> None:
    manager = _runtime_manager()
    manager._config = runtime.QuietHoursConfig(
        enabled=True,
        start="22:00",
        end="07:00",
        max_volume=0.2,
        wake_sound="off",
    )
    period = runtime.QuietPeriod(
        datetime(2026, 9, 13, 22, tzinfo=UTC),
        datetime(2026, 9, 14, 7, tzinfo=UTC),
    )
    manager._publish_state(period)
    assert manager.hass.states.get(runtime._STATE_ENTITY_ID).state == "on"

    monkeypatch.setattr(
        runtime.dt_util,
        "now",
        lambda: datetime(2026, 9, 13, 23, tzinfo=UTC),
    )
    manager.discovery_snapshot = Mock(return_value=[])
    manager._active = {"controls": {"media_player.bedroom": {}}}
    assert manager.snapshot()["owned_controls"] == ["media_player.bedroom"]

    old = Mock()
    manager._unsubscribers = [old]
    manager._config = runtime.QuietHoursConfig(enabled=False)
    manager._reschedule()
    old.assert_called_once_with()
    assert manager._unsubscribers == []

    callbacks = [Mock(), Mock(), Mock()]
    time_change = Mock(side_effect=callbacks[:2])
    interval = Mock(return_value=callbacks[2])
    monkeypatch.setattr(runtime, "async_track_time_change", time_change)
    monkeypatch.setattr(runtime, "async_track_time_interval", interval)
    manager._config = runtime.QuietHoursConfig(
        enabled=True,
        start="22:15",
        end="06:45",
    )
    manager._reschedule()
    assert len(manager._unsubscribers) == 3
    assert time_change.call_count == 2
    interval.assert_called_once_with(
        manager.hass,
        manager._handle_discovery_tick,
        runtime._DISCOVERY_INTERVAL,
    )


async def test_async_get_quiet_hours_reuses_global_manager(monkeypatch) -> None:
    hass = _hass()
    created = []

    class FakeManager:
        def __init__(self, passed_hass) -> None:
            assert passed_hass is hass
            self.setup = AsyncMock()
            created.append(self)

        async def async_setup(self) -> None:
            await self.setup()

    monkeypatch.setattr(runtime, "QuietHoursManager", FakeManager)
    first = await runtime.async_get_quiet_hours(hass)
    second = await runtime.async_get_quiet_hours(hass)
    assert first is second
    assert len(created) == 1
    assert first.setup.await_count == 2


# Base runtime: malformed state and no-op boundaries.


def _bare_runtime_manager(
    hass: Any, config: QuietHoursConfig | None = None
) -> RuntimeQuietHoursManager:
    manager = object.__new__(RuntimeQuietHoursManager)
    manager.hass = hass
    manager._config = config or QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = True
    manager._unsubscribers = []
    return manager


def test_current_switch_returns_none_for_missing_entity() -> None:
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: None))

    assert runtime._current_switch(hass, "switch.missing") is None


def test_discovery_snapshot_serializes_discovered_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_runtime_manager(SimpleNamespace())
    capability = SatelliteCapabilities(
        satellite_entity_id="assist_satellite.kitchen",
        name="Kitchen",
        device_id="device-1",
        media_player_entity_id="media_player.kitchen",
        wake_sound_entity_id="switch.kitchen_wake",
        media_player_source="auto",
        wake_sound_source="manual",
    )
    monkeypatch.setattr(
        runtime,
        "discover_satellite_capabilities",
        lambda _hass, _config: [capability],
    )

    assert manager.discovery_snapshot() == [capability.as_dict()]


@pytest.mark.asyncio
async def test_reconcile_reuses_existing_active_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    existing_controls = {"existing": {"kind": "volume"}}
    manager = _bare_runtime_manager(SimpleNamespace(), QuietHoursConfig(enabled=True))
    manager._active = {
        "period_started_at": period.start.isoformat(),
        "period_ends_at": period.end.isoformat(),
        "applied_at": now.isoformat(),
        "controls": existing_controls,
    }
    original_active = manager._active
    manager._publish_state = MagicMock()
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    monkeypatch.setattr(runtime, "quiet_period_for", lambda *_args: period)
    monkeypatch.setattr(runtime, "discover_satellite_capabilities", lambda *_args: [])

    await manager.async_reconcile(now=now)

    assert manager._active is original_active
    manager._async_restore_locked.assert_not_awaited()
    manager._async_save_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_handles_capabilities_without_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    manager = _bare_runtime_manager(
        SimpleNamespace(),
        QuietHoursConfig(enabled=True, wake_sound="unchanged"),
    )
    manager._active = {
        "period_started_at": period.start.isoformat(),
        "period_ends_at": period.end.isoformat(),
        "applied_at": now.isoformat(),
        "controls": {},
    }
    manager._publish_state = MagicMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    monkeypatch.setattr(runtime, "quiet_period_for", lambda *_args: period)
    monkeypatch.setattr(
        runtime,
        "discover_satellite_capabilities",
        lambda *_args: [
            SatelliteCapabilities(
                satellite_entity_id="assist_satellite.none",
                name="None",
                device_id=None,
                media_player_entity_id=None,
                wake_sound_entity_id=None,
                media_player_source=None,
                wake_sound_source=None,
            ),
            SatelliteCapabilities(
                satellite_entity_id="assist_satellite.wake_only",
                name="Wake only",
                device_id=None,
                media_player_entity_id=None,
                wake_sound_entity_id="switch.wake",
                media_player_source=None,
                wake_sound_source="auto",
            ),
        ],
    )

    await manager.async_reconcile(now=now)

    manager._async_apply_volume_locked.assert_not_awaited()
    manager._async_apply_switch_locked.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_apply_control_skips_already_owned_entity(kind: str) -> None:
    states = SimpleNamespace(get=MagicMock())
    manager = _bare_runtime_manager(SimpleNamespace(states=states))
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = {"entity.test": {"kind": kind}}

    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.test", "entity.test", controls
        )
        manager._async_set_volume.assert_not_awaited()
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.test", "entity.test", False, controls
        )
        manager._async_set_switch.assert_not_awaited()

    states.get.assert_not_called()
    manager._async_save_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_switch_failure_releases_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_runtime_manager(SimpleNamespace())
    controls: dict[str, Any] = {}
    manager._async_save_locked = AsyncMock()
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError("failed"))
    monkeypatch.setattr(runtime, "_current_switch", lambda *_args: True)

    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.test", False, controls
    )

    assert "switch.test" not in controls
    assert manager._async_save_locked.await_count == 2


@pytest.mark.asyncio
async def test_restore_with_non_mapping_controls_clears_active_state() -> None:
    manager = _bare_runtime_manager(SimpleNamespace())
    manager._active = {"controls": ["bad"]}
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restore_ignores_unknown_control_kind_and_continues() -> None:
    manager = _bare_runtime_manager(SimpleNamespace())
    manager._active = {
        "controls": {
            "entity.unknown": {
                "kind": "unknown",
                "original_value": True,
                "quiet_value": False,
            },
            "entity.second": {
                "kind": "unknown",
                "original_value": 0.7,
                "quiet_value": 0.2,
            },
        }
    }
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()
    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


@pytest.mark.parametrize(
    "raw_controls, expected_ids",
    [
        (["not-a-mapping"], None),
        (
            {
                123: {
                    "kind": "volume",
                    "satellite_entity_id": "",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
                "media_player.valid": {
                    "kind": "volume",
                    "satellite_entity_id": "assist_satellite.valid",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
            },
            {"media_player.valid"},
        ),
        (
            {
                "media_player.bad": "not-a-mapping",
                "media_player.valid": {
                    "kind": "volume",
                    "satellite_entity_id": "assist_satellite.valid",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
            },
            {"media_player.valid"},
        ),
    ],
)
def test_normalize_active_rejects_invalid_controls(
    raw_controls: Any, expected_ids: set[str] | None
) -> None:
    manager = _bare_runtime_manager(SimpleNamespace())
    value = {
        "period_started_at": "2026-09-14T22:00:00+00:00",
        "period_ends_at": "2026-09-15T07:00:00+00:00",
        "controls": raw_controls,
    }

    normalized = manager._normalize_active(value)

    if expected_ids is None:
        assert normalized is None
    else:
        assert normalized is not None
        assert set(normalized["controls"]) == expected_ids
