"""Public Quiet Hours runtime with a registered Home Assistant state entity."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .quiet_hours_runtime import (
    _VOLUME_TOLERANCE,
    DEFAULT_END,
    DEFAULT_MAX_VOLUME,
    DEFAULT_START,
    DEFAULT_WAKE_SOUND,
    QuietHoursConfig,
    QuietHoursManager as _RuntimeQuietHoursManager,
    QuietPeriod,
    SatelliteCapabilities,
    SatelliteOverride,
    _config_from_data,
    _current_switch,
    _current_volume,
    _override_map,
    _pick_media_player,
    _pick_wake_sound,
    quiet_period_for,
)

_RUNTIME_KEY = "quiet_hours_manager"
_STATE_UNIQUE_ID = "quiet_hours"
_STATE_FALLBACK_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
SERVICE_ENABLE_QUIET_HOURS = "enable_quiet_hours"
SERVICE_DISABLE_QUIET_HOURS = "disable_quiet_hours"


def discover_satellite_capabilities(
    hass: HomeAssistant, config: QuietHoursConfig
) -> list[SatelliteCapabilities]:
    """Resolve controls for live Assist satellites without registry internals.

    Entity-registry container details and some convenience helpers have changed
    across Home Assistant releases.  The state machine already tells us which
    entities are live, so correlate those states to registry entries one-by-one
    through the stable ``async_get(entity_id)`` API and then match by device id.
    """
    registry = er.async_get(hass)
    overrides = _override_map(config)
    states = list(hass.states.async_all())
    satellites = sorted(
        (state for state in states if state.entity_id.startswith("assist_satellite.")),
        key=lambda item: item.entity_id,
    )

    control_entries = []
    for state in states:
        if not state.entity_id.startswith(("media_player.", "switch.")):
            continue
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.disabled_by is None:
            control_entries.append(entry)

    result: list[SatelliteCapabilities] = []
    for state in satellites:
        satellite = registry.async_get(state.entity_id)
        if satellite is not None and satellite.disabled_by is not None:
            continue
        device_id = satellite.device_id if satellite is not None else None
        same_device = (
            [entry for entry in control_entries if entry.device_id == device_id]
            if device_id is not None
            else []
        )
        override = overrides.get(state.entity_id)
        auto_media = _pick_media_player(hass, same_device)
        auto_wake = _pick_wake_sound(same_device)
        media = (
            override.media_player_entity_id
            if override and override.media_player_entity_id
            else auto_media
        )
        wake = (
            override.wake_sound_entity_id
            if override and override.wake_sound_entity_id
            else auto_wake
        )
        name = (
            state.attributes.get("friendly_name")
            or (satellite.name if satellite is not None else None)
            or (satellite.original_name if satellite is not None else None)
            or state.entity_id
        )
        result.append(
            SatelliteCapabilities(
                satellite_entity_id=state.entity_id,
                name=str(name),
                device_id=device_id,
                media_player_entity_id=media,
                wake_sound_entity_id=wake,
                media_player_source=(
                    "manual"
                    if override and override.media_player_entity_id
                    else "auto"
                    if auto_media
                    else None
                ),
                wake_sound_source=(
                    "manual"
                    if override and override.wake_sound_entity_id
                    else "auto"
                    if auto_wake
                    else None
                ),
                media_player_candidates=tuple(
                    sorted(
                        entry.entity_id
                        for entry in same_device
                        if entry.entity_id.startswith("media_player.")
                    )
                ),
                wake_sound_candidates=tuple(
                    sorted(
                        entry.entity_id
                        for entry in same_device
                        if entry.entity_id.startswith("switch.")
                    )
                ),
            )
        )
    return result


class QuietHoursManager(_RuntimeQuietHoursManager):
    """Quiet Hours manager that registers its automation-facing entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self._registered_state_entity_id: str | None = None

    def _state_entity_id(self) -> str:
        if self._registered_state_entity_id is not None:
            return self._registered_state_entity_id
        registry = er.async_get(self.hass)
        create = getattr(registry, "async_get_or_create", None)
        if not callable(create):
            self._registered_state_entity_id = _STATE_FALLBACK_ENTITY_ID
            return self._registered_state_entity_id
        try:
            entry = create(
                domain="binary_sensor",
                platform=DOMAIN,
                unique_id=_STATE_UNIQUE_ID,
                suggested_object_id="extended_openai_quiet_hours",
                original_name="Quiet Hours",
            )
        except AttributeError as err:
            # Integrations can be set up before the entity registry has loaded its
            # backing collection. Publishing the state must not make startup depend
            # on that internal initialization detail.
            if err.name != "entities":
                raise
            self._registered_state_entity_id = _STATE_FALLBACK_ENTITY_ID
            return self._registered_state_entity_id
        entity_id = getattr(entry, "entity_id", None)
        if not isinstance(entity_id, str):
            self._registered_state_entity_id = _STATE_FALLBACK_ENTITY_ID
            return self._registered_state_entity_id
        self._registered_state_entity_id = entity_id
        return entity_id

    def _publish_state(self, period: QuietPeriod | None) -> None:
        self.hass.states.async_set(
            self._state_entity_id(),
            "on" if period is not None else "off",
            {
                "friendly_name": "Quiet Hours",
                "icon": "mdi:weather-night",
                "enabled": self._config.enabled,
                "start": self._config.start,
                "end": self._config.end,
                "max_volume": self._config.max_volume,
                "wake_sound": self._config.wake_sound,
                "period_started_at": period.start.isoformat() if period else None,
                "period_ends_at": period.end.isoformat() if period else None,
            },
        )

    def discovery_snapshot(self) -> list[dict[str, Any]]:
        return [
            item.as_dict()
            for item in discover_satellite_capabilities(self.hass, self._config)
        ]

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        result["state_entity_id"] = self._state_entity_id()
        return result

    async def async_set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable the daily schedule without changing its policy."""
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        config = self.config.as_dict()
        config["enabled"] = enabled
        return await self.async_update_config(config)

    async def async_reconcile(self, *, now: datetime | None = None) -> None:
        """Reconcile the scheduled policy using cross-version-safe discovery."""
        now = dt_util.now() if now is None else dt_util.as_local(now)
        async with self._lock:
            if not self._initialized:
                return
            period = (
                quiet_period_for(now, self._config.start, self._config.end)
                if self._config.enabled
                else None
            )
            self._publish_state(period)
            if period is None:
                await self._async_restore_locked()
                return

            period_id = period.start.isoformat()
            if (
                self._active is not None
                and self._active.get("period_started_at") != period_id
            ):
                await self._async_restore_locked()

            if self._active is None:
                self._active = {
                    "period_started_at": period_id,
                    "period_ends_at": period.end.isoformat(),
                    "applied_at": now.isoformat(),
                    "controls": {},
                    "observed_controls": [],
                }
                await self._async_save_locked()

            controls = self._active.setdefault("controls", {})
            for capability in discover_satellite_capabilities(self.hass, self._config):
                if capability.media_player_entity_id:
                    await self._async_apply_volume_locked(
                        capability.satellite_entity_id,
                        capability.media_player_entity_id,
                        controls,
                    )
                if (
                    capability.wake_sound_entity_id
                    and self._config.wake_sound != "unchanged"
                ):
                    await self._async_apply_switch_locked(
                        capability.satellite_entity_id,
                        capability.wake_sound_entity_id,
                        self._config.wake_sound == "on",
                        controls,
                    )

    async def _async_apply_volume_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        controls: dict[str, Any],
    ) -> None:
        observed = (
            self._active.setdefault("observed_controls", []) if self._active else []
        )
        if entity_id in controls or entity_id in observed:
            return
        original = _current_volume(self.hass, entity_id)
        if original is None:
            return

        observed.append(entity_id)
        await self._async_save_locked()
        if original <= self._config.max_volume + _VOLUME_TOLERANCE:
            return

        controls[entity_id] = {
            "kind": "volume",
            "satellite_entity_id": satellite_entity_id,
            "original_value": original,
            "quiet_value": self._config.max_volume,
        }
        await self._async_save_locked()
        try:
            await self._async_set_volume(entity_id, self._config.max_volume)
        except Exception:
            controls.pop(entity_id, None)
            observed.remove(entity_id)
            await self._async_save_locked()

    async def _async_apply_switch_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        desired: bool,
        controls: dict[str, Any],
    ) -> None:
        observed = (
            self._active.setdefault("observed_controls", []) if self._active else []
        )
        if entity_id in controls or entity_id in observed:
            return
        original = _current_switch(self.hass, entity_id)
        if original is None:
            return

        observed.append(entity_id)
        await self._async_save_locked()
        if original == desired:
            return

        controls[entity_id] = {
            "kind": "switch",
            "satellite_entity_id": satellite_entity_id,
            "original_value": original,
            "quiet_value": desired,
        }
        await self._async_save_locked()
        try:
            await self._async_set_switch(entity_id, desired)
        except Exception:
            controls.pop(entity_id, None)
            observed.remove(entity_id)
            await self._async_save_locked()

    def _normalize_active(self, value: Any) -> dict[str, Any] | None:
        normalized = super()._normalize_active(value)
        if normalized is None:
            return None
        raw_observed = (
            value.get("observed_controls") if isinstance(value, Mapping) else None
        )
        observed = {item for item in raw_observed or [] if isinstance(item, str)}
        observed.update(normalized.get("controls", {}))
        normalized["observed_controls"] = sorted(observed)
        return normalized

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self.hass.states.async_remove(self._state_entity_id())


async def _async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Allow system automations and require admins for user-originated actions."""
    user_id = getattr(getattr(call, "context", None), "user_id", None)
    if user_id is None:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError("Administrator permission is required")


def _register_quiet_hours_actions(hass: HomeAssistant) -> None:
    """Register global schedule enable/disable actions exactly once."""
    if not hass.services.has_service(DOMAIN, SERVICE_ENABLE_QUIET_HOURS):

        async def enable_quiet_hours(call: ServiceCall) -> None:
            await _async_require_admin(hass, call)
            manager = await async_get_quiet_hours(hass)
            await manager.async_set_enabled(True)

        hass.services.async_register(
            DOMAIN,
            SERVICE_ENABLE_QUIET_HOURS,
            enable_quiet_hours,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DISABLE_QUIET_HOURS):

        async def disable_quiet_hours(call: ServiceCall) -> None:
            await _async_require_admin(hass, call)
            manager = await async_get_quiet_hours(hass)
            await manager.async_set_enabled(False)

        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_QUIET_HOURS,
            disable_quiet_hours,
        )


async def async_get_quiet_hours(hass: HomeAssistant) -> QuietHoursManager:
    """Return the initialized integration-global Quiet Hours manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_RUNTIME_KEY)
    if not isinstance(manager, QuietHoursManager):
        manager = QuietHoursManager(hass)
        domain_data[_RUNTIME_KEY] = manager
    await manager.async_setup()
    _register_quiet_hours_actions(hass)
    return manager


__all__ = [
    "DEFAULT_END",
    "DEFAULT_MAX_VOLUME",
    "DEFAULT_START",
    "DEFAULT_WAKE_SOUND",
    "SERVICE_DISABLE_QUIET_HOURS",
    "SERVICE_ENABLE_QUIET_HOURS",
    "QuietHoursConfig",
    "QuietHoursManager",
    "QuietPeriod",
    "SatelliteCapabilities",
    "SatelliteOverride",
    "_config_from_data",
    "async_get_quiet_hours",
    "discover_satellite_capabilities",
    "quiet_period_for",
]
