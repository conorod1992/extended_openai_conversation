"""Public Quiet Hours runtime with a registered Home Assistant state entity."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from homeassistant.core import Context, HomeAssistant, ServiceCall, State
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
        self._published_state: State | None = None

    def _state_entity_id(self) -> str:
        registry = er.async_get(self.hass)
        try:
            lookup = getattr(registry, "async_get_entity_id", None)
            if callable(lookup):
                current = lookup("binary_sensor", DOMAIN, _STATE_UNIQUE_ID)
                if isinstance(current, str):
                    self._registered_state_entity_id = current
                    return current
            create = getattr(registry, "async_get_or_create", None)
            if not callable(create):
                self._registered_state_entity_id = _STATE_FALLBACK_ENTITY_ID
                return self._registered_state_entity_id
            entry = create(
                domain="binary_sensor",
                platform=DOMAIN,
                unique_id=_STATE_UNIQUE_ID,
                suggested_object_id="extended_openai_quiet_hours",
                original_name="Quiet Hours",
            )
        except AttributeError as err:
            # Integrations can be set up before the registry collection is loaded.
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

    def _remove_previous_publication(self, entity_id: str) -> None:
        previous = self._published_state
        if (
            previous is not None
            and previous.entity_id != entity_id
            and self.hass.states.get(previous.entity_id) is previous
        ):
            self.hass.states.async_remove(previous.entity_id)

    def _publish_state(self, period: QuietPeriod | None) -> None:
        entity_id = self._state_entity_id()
        self._remove_previous_publication(entity_id)
        self.hass.states.async_set(
            entity_id,
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

        self._published_state = self.hass.states.get(entity_id)

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
            self._migrate_control_entity_ids()
            period = (
                quiet_period_for(now, self._config.start, self._config.end)
                if self._config.enabled
                else None
            )
            self._publish_state(period)
            if period is None:
                await self._async_restore_locked()
                if (
                    not self._config.enabled
                    and self._active is None
                    and self._unsubscribers
                ):
                    self._reschedule()
                return

            period_id = period.start.isoformat()
            if (
                self._active is not None
                and self._active.get("period_started_at") != period_id
            ):
                await self._async_restore_locked()
                if self._active is not None:
                    self._active.update(
                        {
                            "period_started_at": period_id,
                            "period_ends_at": period.end.isoformat(),
                            "applied_at": now.isoformat(),
                        }
                    )
                    await self._async_save_locked()
            await self._async_retry_pending_restores_locked()

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
        observed = (self._active or {}).get("observed_controls", [])
        pending = (self._active or {}).get("pending_controls", {})
        if (
            entity_id in controls
            and controls[entity_id].get("application_state") != "prepared"
        ) or (
            entity_id in observed
            and entity_id not in pending
            and entity_id not in controls
        ):
            return
        original = _current_volume(self.hass, entity_id)
        if original is None:
            return
        control = await self._async_prepare_control_locked(
            satellite_entity_id,
            entity_id,
            controls,
            "volume",
            original,
            self._config.max_volume,
            original > self._config.max_volume + _VOLUME_TOLERANCE,
        )
        if control is None:
            return
        if not await self._async_revalidate_control_locked(
            entity_id, controls, control
        ):
            return
        assert self._active is not None
        observed = self._active["observed_controls"]
        try:
            await self._async_set_volume(entity_id, self._config.max_volume)
        except Exception as err:
            # A service error does not prove that the device stayed unchanged.
            # Keep the persisted baseline unless HA confirms no change occurred.
            current = _current_volume(self.hass, entity_id)
            unchanged = (
                current is not None
                and abs(current - control["original_value"]) <= _VOLUME_TOLERANCE
            )
            self._log_control_failure(
                "apply",
                entity_id,
                control["kind"],
                err,
                ownership="released" if unchanged else "retained",
            )
            if unchanged:
                controls.pop(entity_id, None)
                observed.remove(entity_id)
                await self._async_save_control_state_locked()
            # A failed service acknowledgement leaves any retained intent
            # prepared; later reconciliation must attribute its actual effect.
            return

        if entity_id in controls:
            control["application_state"] = "applied"
            await self._async_save_control_state_locked()

    async def _async_apply_switch_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        desired: bool,
        controls: dict[str, Any],
    ) -> None:
        observed = (self._active or {}).get("observed_controls", [])
        pending = (self._active or {}).get("pending_controls", {})
        if (
            entity_id in controls
            and controls[entity_id].get("application_state") != "prepared"
        ) or (
            entity_id in observed
            and entity_id not in pending
            and entity_id not in controls
        ):
            return
        original = _current_switch(self.hass, entity_id)
        if original is None:
            return
        control = await self._async_prepare_control_locked(
            satellite_entity_id,
            entity_id,
            controls,
            "switch",
            original,
            desired,
            original != desired,
        )
        if control is None:
            return
        if not await self._async_revalidate_control_locked(
            entity_id, controls, control
        ):
            return
        assert self._active is not None
        observed = self._active["observed_controls"]
        try:
            await self._async_set_switch(entity_id, desired)
        except Exception as err:
            unchanged = (
                _current_switch(self.hass, entity_id) is control["original_value"]
            )
            self._log_control_failure(
                "apply",
                entity_id,
                control["kind"],
                err,
                ownership="released" if unchanged else "retained",
            )
            if unchanged:
                controls.pop(entity_id, None)
                observed.remove(entity_id)
                await self._async_save_control_state_locked()
            return

        if entity_id in controls:
            control["application_state"] = "applied"
            await self._async_save_control_state_locked()

    async def _async_prepare_control_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        controls: dict[str, Any],
        kind: str,
        original: float | bool,
        desired: float | bool,
        needs_action: bool,
    ) -> dict[str, Any] | None:
        """Persist observation and ownership without losing a retryable baseline."""
        assert self._active is not None
        observed = self._active.setdefault("observed_controls", [])
        pending = self._active.setdefault("pending_controls", {})
        existing = controls.get(entity_id)
        if existing is not None:
            if existing.get("application_state") != "prepared":
                return None
            same_control = (
                existing["kind"] == kind
                and existing["satellite_entity_id"] == satellite_entity_id
            )
            at_quiet_value = self._control_values_equal(
                kind, original, existing["quiet_value"]
            )
            state = self.hass.states.get(entity_id)
            context_id = existing.get("application_context_id")
            own_effect = bool(context_id and state and state.context.id == context_id)
            if same_control and at_quiet_value and own_effect:
                # The action may have completed before acknowledgement was lost.
                # Reconcile attainment without replaying the device operation.
                existing["application_state"] = "applied"
                await self._async_save_control_state_locked()
                return None
            if (
                at_quiet_value
                or not same_control
                or not self._control_values_equal(
                    kind, original, existing["original_value"]
                )
                or not self._baseline_context_matches(entity_id, existing)
            ):
                controls.pop(entity_id)
                await self._async_save_control_state_locked()
                return None
            return cast(dict[str, Any], existing)
        if entity_id in observed and entity_id not in pending:
            return None
        intent = pending.get(entity_id)
        if intent is not None:
            same_baseline = (
                abs(original - intent["original_value"]) <= _VOLUME_TOLERANCE
                if kind == "volume"
                else original == intent["original_value"]
            )
            if (
                intent["kind"] != kind
                or intent["satellite_entity_id"] != satellite_entity_id
                or not same_baseline
                or not self._baseline_context_matches(entity_id, intent, legacy=True)
            ):
                # A changed device association or manual value ends this attempt;
                # keep observation so we do not claim or overwrite that change.
                pending.pop(entity_id)
                await self._async_save_control_state_locked()
                return None
        else:
            try:
                entry = er.async_get(self.hass).async_get(entity_id)
            except TypeError:
                entry = None
            identity = [
                entity_id.partition(".")[0],
                getattr(entry, "platform", None),
                getattr(entry, "unique_id", None),
            ]
            intent = {
                "kind": kind,
                "satellite_entity_id": satellite_entity_id,
                "original_value": original,
                "quiet_value": desired,
                "baseline_context_id": self._control_context_id(entity_id),
                **(
                    {"registry_identity": identity}
                    if all(isinstance(item, str) and item for item in identity)
                    else {}
                ),
            }
            observed.append(entity_id)
        if needs_action:
            pending[entity_id] = {**intent, "quiet_value": desired}
        else:
            pending.pop(entity_id, None)
        await self._async_save_control_state_locked()
        if not needs_action:
            return None
        controls[entity_id] = {
            **pending.pop(entity_id),
            "application_state": "prepared",
            "application_context_id": Context().id,
        }
        await self._async_save_control_state_locked()
        return cast(dict[str, Any], controls[entity_id])

    def _control_context_id(self, entity_id: str) -> str | None:
        states = getattr(self.hass, "states", None)
        state = states.get(entity_id) if states is not None else None
        return getattr(getattr(state, "context", None), "id", None)

    def _baseline_context_matches(
        self, entity_id: str, control: dict[str, Any], *, legacy: bool = False
    ) -> bool:
        baseline = control.get("baseline_context_id")
        if baseline is None:
            return legacy
        return bool(self._control_context_id(entity_id) == baseline)

    @staticmethod
    def _control_values_equal(kind: str, left: Any, right: Any) -> bool:
        if kind == "volume":
            return left is not None and abs(left - right) <= _VOLUME_TOLERANCE
        return left is right

    async def _async_set_volume(self, entity_id: str, volume_level: float) -> None:
        await self._async_call_control_service(
            "media_player", "volume_set", entity_id, {"volume_level": volume_level}
        )

    async def _async_restore_locked(self, *, pending_only: bool = False) -> None:
        self._migrate_control_entity_ids()
        if self._active is not None:
            controls = self._active.get("controls", {})
            for entity_id, control in list(controls.items()):
                if pending_only and not control.get("restoration_pending"):
                    continue
                if control.get("application_state") != "prepared":
                    continue
                state = self.hass.states.get(entity_id)
                context_id = control.get("application_context_id")
                if state is None or state.state in {"unavailable", "unknown"}:
                    continue
                if not context_id or state.context.id != context_id:
                    # Ending a period must not restore a value merely because an
                    # unacknowledged intent happens to match an independent effect.
                    controls.pop(entity_id)
        await super()._async_restore_locked(pending_only=pending_only)

    def _migrate_control_entity_ids(self) -> None:
        """Follow registry identities when owned speakers or switches are renamed."""
        if self._active is None:
            return
        for key in ("controls", "pending_controls"):
            controls = self._active.get(key, {})
            for old_id, control in list(controls.items()):
                identity = control.get("registry_identity")
                if not isinstance(identity, list) or len(identity) != 3:
                    continue
                registry = er.async_get(self.hass)
                new_id = registry.async_get_entity_id(*identity)
                if (
                    not isinstance(new_id, str)
                    or new_id == old_id
                    or new_id in controls
                ):
                    continue
                controls[new_id] = controls.pop(old_id)
                self._active["observed_controls"] = [
                    new_id if entity_id == old_id else entity_id
                    for entity_id in self._active.get("observed_controls", [])
                ]

    async def _async_set_switch(self, entity_id: str, enabled: bool) -> None:
        await self._async_call_control_service(
            "switch", "turn_on" if enabled else "turn_off", entity_id, {}
        )

    async def _async_call_control_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> None:
        """Associate an observable HA effect with its persisted prepared intent."""
        control = (self._active or {}).get("controls", {}).get(entity_id, {})
        context_id = control.get("application_context_id")
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
            context=Context(id=context_id) if context_id else None,
        )

    async def _async_revalidate_control_locked(
        self, entity_id: str, controls: dict[str, Any], control: dict[str, Any]
    ) -> bool:
        """Release prepared ownership if persistence outlived its observation."""
        current = (
            _current_volume(self.hass, entity_id)
            if control["kind"] == "volume"
            else _current_switch(self.hass, entity_id)
        )
        if self._control_values_equal(
            control["kind"], current, control["original_value"]
        ) and self._baseline_context_matches(entity_id, control, legacy=True):
            return True
        controls.pop(entity_id, None)
        # Preserve observation so subsequent reconciliation cannot reclaim a
        # newer independent value during the same quiet period.
        await self._async_save_control_state_locked()
        return False

    async def _async_save_control_state_locked(self) -> None:
        """A reported control-write failure must reconcile its actual generation."""
        try:
            await self._async_save_locked()
        except Exception:
            try:
                persisted = await self._store.async_load()
                if not isinstance(persisted, Mapping):
                    raise ValueError("Quiet Hours control state cannot be reconciled")
                active = self._normalize_active(persisted.get("active"))
                if active is None and persisted.get("active") is not None:
                    raise ValueError("Quiet Hours control state is invalid")
                self._active = active
            except Exception:
                self._active = None
                self._initialized = False
            raise

    def _normalize_active(self, value: Any) -> dict[str, Any] | None:
        normalized = super()._normalize_active(value)
        if normalized is None:
            return None
        raw_controls = value.get("controls") or {}
        for entity_id, control in normalized["controls"].items():
            self._normalize_baseline_context(control, raw_controls.get(entity_id, {}))
            phase = raw_controls.get(entity_id, {}).get("application_state")
            if phase is not None:
                if phase not in {"prepared", "applied"}:
                    raise ValueError("Quiet Hours control application state is invalid")
                control["application_state"] = phase
                context_id = raw_controls.get(entity_id, {}).get(
                    "application_context_id"
                )
                if context_id is not None:
                    if not isinstance(context_id, str) or not context_id:
                        raise ValueError("Quiet Hours application context is invalid")
                    control["application_context_id"] = context_id
        raw_observed = (
            value.get("observed_controls") if isinstance(value, Mapping) else None
        )
        observed = {item for item in raw_observed or [] if isinstance(item, str)}
        observed.update(normalized.get("controls", {}))
        raw_pending = (
            value.get("pending_controls") if isinstance(value, Mapping) else None
        )
        pending_state = super()._normalize_active(
            {**value, "controls": raw_pending or {}}
        )
        pending = {
            entity_id: control
            for entity_id, control in (pending_state or {}).get("controls", {}).items()
            if entity_id not in normalized["controls"]
        }
        for entity_id, control in pending.items():
            self._normalize_baseline_context(
                control, (raw_pending or {}).get(entity_id, {})
            )
        observed.update(pending)
        normalized["observed_controls"] = sorted(observed)
        normalized["pending_controls"] = pending
        return normalized

    @staticmethod
    def _normalize_baseline_context(
        control: dict[str, Any], raw: dict[str, Any]
    ) -> None:
        context_id = raw.get("baseline_context_id")
        if context_id is not None:
            if not isinstance(context_id, str) or not context_id:
                raise ValueError("Quiet Hours baseline context is invalid")
            control["baseline_context_id"] = context_id

    async def async_shutdown(self) -> None:
        async with self._lock:
            for unsubscribe in self._unsubscribers:
                unsubscribe()
            self._unsubscribers.clear()
            try:
                await self._async_restore_locked()
            finally:
                entity_id = self._state_entity_id()
                self._remove_previous_publication(entity_id)
                self.hass.states.async_remove(entity_id)


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
    if hass.data.get(f"{DOMAIN}.removed") is True:
        raise HomeAssistantError("Extended OpenAI has been removed")
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
