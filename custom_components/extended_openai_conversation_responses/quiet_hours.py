"""Public Quiet Hours runtime with a registered Home Assistant state entity."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .global_runtime import (
    global_runtime_available,
    global_runtime_lock,
    require_global_runtime,
)
from .operation_settlement import async_settle_owned_operation
from .operational_errors import log_handled_failure
from .quiet_hours_runtime import (
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
    discover_satellite_capabilities,
    quiet_period_for,
)

_RUNTIME_KEY = "quiet_hours_manager"
_STATE_UNIQUE_ID = "quiet_hours"
_STATE_FALLBACK_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
SERVICE_ENABLE_QUIET_HOURS = "enable_quiet_hours"
SERVICE_DISABLE_QUIET_HOURS = "disable_quiet_hours"


class QuietHoursManager(_RuntimeQuietHoursManager):
    """Quiet Hours manager that registers its automation-facing entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self._registered_state_entity_id: str | None = None
        self._published_state: State | None = None
        self._registry_unsubscribe: Callable[[], None] | None = None
        self._setup_lock = asyncio.Lock()

    async def async_setup(self) -> None:
        async with self._setup_lock:
            if self._initialized:
                await self._async_setup_generation()
            else:
                await async_settle_owned_operation(self._async_setup_generation())

    async def _async_setup_generation(self) -> None:
        try:
            await self._async_setup_and_listen()
        except Exception as err:
            reason = self._startup_unavailable_reason or (
                "Quiet Hours could not finish initialization. Saved restoration "
                "data is retained. Restart Home Assistant after resolving the failure."
            )
            self._startup_unavailable_reason = reason
            log_handled_failure(logging.getLogger(__name__), reason, err)
            async with self._lock:
                self._cancel_scheduled_callbacks()
                self._detach_timezone_listener()
                self._detach_registry_listener()
                try:
                    self._remove_owned_publication()
                except Exception as cleanup_error:
                    log_handled_failure(
                        logging.getLogger(__name__),
                        "Unable to remove the Quiet Hours state publication",
                        cleanup_error,
                    )

    async def _async_setup_and_listen(self) -> None:
        await super().async_setup()
        if self._stopping or self.startup_unavailable:
            return
        if self._registry_unsubscribe is None:
            self._registry_unsubscribe = self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._async_registry_updated
            )

    async def _async_registry_updated(self, event: Any) -> None:
        """Republish renamed/disabled state even when the schedule is disabled."""
        if (
            self._registry_unsubscribe is None
            or self.hass.data.get(f"{DOMAIN}.removed") is True
        ):
            return
        entity_id = event.data.get("entity_id")
        current = er.async_get(self.hass).async_get_entity_id(
            "binary_sensor", DOMAIN, _STATE_UNIQUE_ID
        )
        changes = event.data.get("changes", {})
        old_id = changes.get("entity_id") if isinstance(changes, dict) else None
        selected = self._selected_entity_ids()
        if (
            entity_id in {self._registered_state_entity_id, current}
            or entity_id in selected
            or old_id in selected
        ):
            try:
                await self.async_reconcile()
            except (OSError, ValueError, HomeAssistantError) as err:
                # Persisted selection/ownership recovery remains pending. A
                # registry notification must not create an unobserved task error.
                log_handled_failure(
                    logging.getLogger(__name__),
                    "Quiet Hours registry reconciliation deferred",
                    err,
                )

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
        registry = er.async_get(self.hass)
        lookup = getattr(registry, "async_get", None)
        try:
            entry = lookup(entity_id) if callable(lookup) else None
        except AttributeError as err:
            # Registry storage may not be loaded during early integration setup.
            # Defer publication until we can honor the saved disablement state.
            if err.name not in {"entities", "_entities_data"}:
                raise
            return
        if entry is not None and entry.disabled_by is not None:
            if self._published_state is not None and (
                self.hass.states.get(entity_id) is self._published_state
            ):
                self.hass.states.async_remove(entity_id)
            self._published_state = None
            return
        registry_name = getattr(entry, "name", None)
        friendly_name = (
            registry_name
            if isinstance(registry_name, str) and registry_name.strip()
            else "Quiet Hours"
        )
        self.hass.states.async_set(
            entity_id,
            "on" if period is not None else "off",
            {
                "friendly_name": friendly_name,
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

    def _discover_capabilities(self) -> list[SatelliteCapabilities]:
        return self._filter_selected_capabilities(
            discover_satellite_capabilities(self.hass, self._config)
        )

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        if not self.startup_unavailable:
            result["state_entity_id"] = self._state_entity_id()
        return result

    def _detach_registry_listener(self) -> None:
        unsubscribe = self._registry_unsubscribe
        self._registry_unsubscribe = None
        if unsubscribe is not None:
            self._unsubscribe_callback(unsubscribe)

    async def async_shutdown(self) -> None:
        self._stopping = True
        self._detach_registry_listener()
        await super().async_shutdown()

    def _remove_owned_publication(self) -> None:
        published = self._published_state
        self._published_state = None
        if published is not None and self.hass.states.get(published.entity_id) is published:
            self.hass.states.async_remove(published.entity_id)

    def _unpublish_state(self) -> None:
        entity_id = self._state_entity_id()
        self._remove_previous_publication(entity_id)
        self.hass.states.async_remove(entity_id)


def async_remove_quiet_hours_entity(hass: HomeAssistant) -> None:
    """Delete only EOAI's registry identity after final provider removal."""
    registry = er.async_get(hass)
    try:
        entity_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, _STATE_UNIQUE_ID
        )
    except AttributeError as err:
        # Partial bootstrap has no loaded registry collection to remove from.
        if err.name != "entities":
            raise
        return
    if entity_id is not None:
        hass.states.async_remove(entity_id)
        registry.async_remove(entity_id)


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
            try:
                await manager.async_set_enabled(True)
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_ENABLE_QUIET_HOURS,
            enable_quiet_hours,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DISABLE_QUIET_HOURS):

        async def disable_quiet_hours(call: ServiceCall) -> None:
            await _async_require_admin(hass, call)
            manager = await async_get_quiet_hours(hass)
            try:
                await manager.async_set_enabled(False)
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_QUIET_HOURS,
            disable_quiet_hours,
        )


async def async_restore_saved_quiet_hours(hass: HomeAssistant) -> None:
    """Attempt durable restoration once before any entry/provider can load.

    This manager is never cached or exposed to public actions. Failed/unavailable
    controls remain in the existing private store for a later recovery attempt.
    """
    async with global_runtime_lock(hass):
        if global_runtime_available(hass):
            return
        try:
            manager = _RuntimeQuietHoursManager(hass)
            await manager.async_restore_saved_controls()
        except Exception as err:
            log_handled_failure(
                logging.getLogger(__name__),
                "Quiet Hours startup restoration deferred",
                err,
            )


async def async_get_quiet_hours(hass: HomeAssistant) -> QuietHoursManager:
    """Return the initialized integration-global Quiet Hours manager."""
    if hass.data.get(f"{DOMAIN}.removed") is True:
        raise HomeAssistantError("Extended OpenAI has been removed")
    require_global_runtime(hass)
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_RUNTIME_KEY)
    if not isinstance(manager, QuietHoursManager):
        manager = QuietHoursManager(hass)
        domain_data[_RUNTIME_KEY] = manager
    await manager.async_setup()
    require_global_runtime(hass)
    current: object = domain_data.get(_RUNTIME_KEY)
    if current is not manager:
        raise HomeAssistantError("Quiet Hours is shutting down")
    try:
        _register_quiet_hours_actions(hass)
    except Exception as err:
        log_handled_failure(
            logging.getLogger(__name__), "Unable to register Quiet Hours actions", err
        )
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
