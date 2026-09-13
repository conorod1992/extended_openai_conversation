"""Residual behavioural coverage for the public Quiet Hours runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import quiet_hours
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    SERVICE_DISABLE_QUIET_HOURS,
    SERVICE_ENABLE_QUIET_HOURS,
    QuietHoursManager,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursConfig,
    QuietPeriod,
    SatelliteCapabilities,
)


def _state(entity_id: str, state: str = "idle", **attributes: Any) -> Any:
    return SimpleNamespace(entity_id=entity_id, state=state, attributes=attributes)


def _entry(
    entity_id: str,
    *,
    domain: str,
    device_id: str | None = None,
    disabled_by: object | None = None,
    platform: str = "esphome",
    name: str | None = None,
    original_name: str | None = None,
) -> Any:
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=disabled_by,
        platform=platform,
        name=name,
        original_name=original_name,
    )


def _bare_manager(hass: Any, config: QuietHoursConfig | None = None) -> QuietHoursManager:
    manager = object.__new__(QuietHoursManager)
    manager.hass = hass
    manager._config = config or QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = True
    manager._unsubscribers = []
    manager._registered_state_entity_id = None
    return manager


def test_discovery_filters_disabled_and_registryless_controls_and_names_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = [
        _state("assist_satellite.disabled"),
        _state("assist_satellite.named"),
        _state("assist_satellite.registryless"),
        _state("media_player.enabled", volume_level=0.5),
        _state("media_player.disabled", volume_level=0.5),
        _state("media_player.registryless", volume_level=0.5),
        _state("switch.enabled", state="on"),
    ]
    entries = {
        "assist_satellite.disabled": _entry(
            "assist_satellite.disabled",
            domain="assist_satellite",
            device_id="disabled-device",
            disabled_by="user",
        ),
        "assist_satellite.named": _entry(
            "assist_satellite.named",
            domain="assist_satellite",
            device_id="device-1",
            name="Registry Name",
        ),
        "media_player.enabled": _entry(
            "media_player.enabled", domain="media_player", device_id="device-1"
        ),
        "media_player.disabled": _entry(
            "media_player.disabled",
            domain="media_player",
            device_id="device-1",
            disabled_by="user",
        ),
        "switch.enabled": _entry(
            "switch.enabled",
            domain="switch",
            device_id="device-1",
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


@pytest.mark.parametrize("mode", ["missing", "unready", "bad-id"])
def test_state_entity_registration_falls_back_safely(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode == "missing":
        registry = SimpleNamespace()
    elif mode == "unready":
        def create(**_kwargs: Any) -> Any:
            raise AttributeError("entities")

        registry = SimpleNamespace(async_get_or_create=create)
    else:
        registry = SimpleNamespace(
            async_get_or_create=lambda **_kwargs: SimpleNamespace(entity_id=None)
        )
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _hass: registry)
    manager = _bare_manager(SimpleNamespace())

    assert manager._state_entity_id() == quiet_hours._STATE_FALLBACK_ENTITY_ID
    assert manager._state_entity_id() == quiet_hours._STATE_FALLBACK_ENTITY_ID


def test_state_entity_registration_propagates_unrelated_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def create(**_kwargs: Any) -> Any:
        exc = AttributeError("other")
        exc.name = "other"
        raise exc

    monkeypatch.setattr(
        quiet_hours.er,
        "async_get",
        lambda _hass: SimpleNamespace(async_get_or_create=create),
    )
    manager = _bare_manager(SimpleNamespace())

    with pytest.raises(AttributeError, match="other"):
        manager._state_entity_id()


def test_state_entity_registration_and_state_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = MagicMock(return_value=SimpleNamespace(entity_id="binary_sensor.quiet"))
    monkeypatch.setattr(
        quiet_hours.er,
        "async_get",
        lambda _hass: SimpleNamespace(async_get_or_create=create),
    )
    states = SimpleNamespace(async_set=MagicMock())
    manager = _bare_manager(SimpleNamespace(states=states))
    manager._config = QuietHoursConfig(enabled=True, max_volume=0.2, wake_sound="off")
    period = QuietPeriod(
        datetime(2026, 9, 14, 22, tzinfo=UTC),
        datetime(2026, 9, 15, 7, tzinfo=UTC),
    )

    manager._publish_state(period)
    manager._publish_state(None)

    assert create.call_count == 1
    first = states.async_set.call_args_list[0]
    assert first.args[0:2] == ("binary_sensor.quiet", "on")
    assert first.args[2]["period_started_at"] == period.start.isoformat()
    assert states.async_set.call_args_list[1].args[1] == "off"


async def test_set_enabled_validates_and_delegates() -> None:
    manager = _bare_manager(SimpleNamespace())
    manager._config = QuietHoursConfig(enabled=False, max_volume=0.2)
    manager.async_update_config = AsyncMock(return_value={"ok": True})

    with pytest.raises(ValueError, match="boolean"):
        await manager.async_set_enabled(1)  # type: ignore[arg-type]

    assert await manager.async_set_enabled(True) == {"ok": True}
    assert manager.async_update_config.await_args.args[0]["enabled"] is True


async def test_reconcile_returns_before_initialization_and_restores_when_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_manager(SimpleNamespace())
    manager._publish_state = MagicMock()
    manager._async_restore_locked = AsyncMock()
    manager._initialized = False

    await manager.async_reconcile(now=datetime(2026, 9, 14, 12, tzinfo=UTC))
    manager._publish_state.assert_not_called()

    manager._initialized = True
    manager._config = QuietHoursConfig(enabled=False)
    await manager.async_reconcile(now=datetime(2026, 9, 14, 12, tzinfo=UTC))
    manager._publish_state.assert_called_once_with(None)
    manager._async_restore_locked.assert_awaited_once_with()


async def test_reconcile_rolls_period_and_applies_discovered_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    manager = _bare_manager(SimpleNamespace())
    manager._config = QuietHoursConfig(enabled=True, max_volume=0.2, wake_sound="off")
    manager._active = {
        "period_started_at": "old-period",
        "controls": {},
        "observed_controls": [],
    }
    manager._publish_state = MagicMock()

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
    manager._async_apply_volume_locked.assert_awaited_once()
    manager._async_apply_switch_locked.assert_awaited_once_with(
        "assist_satellite.kitchen", "switch.kitchen_wake", False, {}
    )


@pytest.mark.parametrize("already_owned", [True, False])
async def test_volume_apply_skips_owned_or_unreadable_controls(
    monkeypatch: pytest.MonkeyPatch, already_owned: bool
) -> None:
    manager = _bare_manager(SimpleNamespace(), QuietHoursConfig(max_volume=0.2))
    manager._active = {"controls": {}, "observed_controls": []}
    controls = manager._active["controls"]
    if already_owned:
        controls["media_player.test"] = {"kind": "volume"}
    else:
        monkeypatch.setattr(quiet_hours, "_current_volume", lambda *_args: None)
    manager._async_save_locked = AsyncMock()

    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.test", controls
    )

    manager._async_save_locked.assert_not_awaited()


async def test_volume_apply_observes_noop_and_rolls_back_failed_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_manager(SimpleNamespace(), QuietHoursConfig(max_volume=0.2))
    manager._active = {"controls": {}, "observed_controls": []}
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    monkeypatch.setattr(quiet_hours, "_current_volume", lambda *_args: 0.15)

    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.quiet", manager._active["controls"]
    )
    assert manager._active["observed_controls"] == ["media_player.quiet"]
    manager._async_set_volume.assert_not_awaited()

    monkeypatch.setattr(quiet_hours, "_current_volume", lambda *_args: 0.8)
    manager._async_set_volume.side_effect = RuntimeError("service failed")
    await manager._async_apply_volume_locked(
        "assist_satellite.test", "media_player.loud", manager._active["controls"]
    )
    assert "media_player.loud" not in manager._active["controls"]
    assert "media_player.loud" not in manager._active["observed_controls"]
    assert manager._async_save_locked.await_count == 4


async def test_switch_apply_observes_noop_and_rolls_back_failed_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_manager(SimpleNamespace(), QuietHoursConfig(wake_sound="off"))
    manager._active = {"controls": {}, "observed_controls": []}
    manager._async_save_locked = AsyncMock()
    manager._async_set_switch = AsyncMock()
    monkeypatch.setattr(quiet_hours, "_current_switch", lambda *_args: False)

    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.already_off", False, manager._active["controls"]
    )
    assert manager._active["observed_controls"] == ["switch.already_off"]
    manager._async_set_switch.assert_not_awaited()

    monkeypatch.setattr(quiet_hours, "_current_switch", lambda *_args: True)
    manager._async_set_switch.side_effect = RuntimeError("service failed")
    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.on", False, manager._active["controls"]
    )
    assert "switch.on" not in manager._active["controls"]
    assert "switch.on" not in manager._active["observed_controls"]
    assert manager._async_save_locked.await_count == 4


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_apply_skips_already_observed_control(kind: str) -> None:
    manager = _bare_manager(SimpleNamespace())
    manager._active = {"controls": {}, "observed_controls": ["entity.test"]}
    manager._async_save_locked = AsyncMock()
    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.test", "entity.test", manager._active["controls"]
        )
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.test", "entity.test", False, manager._active["controls"]
        )
    manager._async_save_locked.assert_not_awaited()


def test_normalize_active_adds_owned_controls_to_observed_set() -> None:
    manager = _bare_manager(SimpleNamespace())
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


async def test_shutdown_unsubscribes_clears_and_removes_registered_state() -> None:
    first = MagicMock()
    second = MagicMock()
    states = SimpleNamespace(async_remove=MagicMock())
    manager = _bare_manager(SimpleNamespace(states=states))
    manager._registered_state_entity_id = "binary_sensor.quiet"
    manager._unsubscribers = [first, second]

    await manager.async_shutdown()

    first.assert_called_once_with()
    second.assert_called_once_with()
    assert manager._unsubscribers == []
    states.async_remove.assert_called_once_with("binary_sensor.quiet")


async def test_require_admin_allows_system_and_admin_but_rejects_others() -> None:
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
                hass, SimpleNamespace(context=SimpleNamespace(user_id="restricted"))
            )


class _Services:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.handlers: dict[str, Any] = {}

    def has_service(self, _domain: str, service: str) -> bool:
        return service in self.existing

    def async_register(self, _domain: str, service: str, handler: Any) -> None:
        self.handlers[service] = handler
        self.existing.add(service)


async def test_action_registration_is_idempotent_and_handlers_toggle_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    hass = SimpleNamespace(services=services, auth=SimpleNamespace(async_get_user=AsyncMock()))
    manager = SimpleNamespace(async_set_enabled=AsyncMock())
    monkeypatch.setattr(
        quiet_hours, "async_get_quiet_hours", AsyncMock(return_value=manager)
    )

    quiet_hours._register_quiet_hours_actions(hass)
    quiet_hours._register_quiet_hours_actions(hass)
    assert set(services.handlers) == {
        SERVICE_ENABLE_QUIET_HOURS,
        SERVICE_DISABLE_QUIET_HOURS,
    }

    call = SimpleNamespace(context=SimpleNamespace(user_id=None))
    await services.handlers[SERVICE_ENABLE_QUIET_HOURS](call)
    await services.handlers[SERVICE_DISABLE_QUIET_HOURS](call)
    assert manager.async_set_enabled.await_args_list[0].args == (True,)
    assert manager.async_set_enabled.await_args_list[1].args == (False,)


async def test_async_get_quiet_hours_reuses_valid_manager_and_replaces_invalid(
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
