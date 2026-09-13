"""Public Quiet Hours runtime with a registered Home Assistant state entity."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .quiet_hours_runtime import *  # noqa: F403
from .quiet_hours_runtime import (
    QuietHoursManager as _RuntimeQuietHoursManager,
    QuietPeriod,
)

_RUNTIME_KEY = "quiet_hours_manager"
_STATE_UNIQUE_ID = "quiet_hours"
_STATE_FALLBACK_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"


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
            # Home Assistant's real entity registry always exposes this method.
            # Retaining a deterministic fallback keeps reduced test/runtime stubs
            # usable without weakening normal registry-backed identity.
            self._registered_state_entity_id = _STATE_FALLBACK_ENTITY_ID
            return self._registered_state_entity_id
        entry = create(
            domain="binary_sensor",
            platform=DOMAIN,
            unique_id=_STATE_UNIQUE_ID,
            suggested_object_id="extended_openai_quiet_hours",
            original_name="Quiet Hours",
        )
        self._registered_state_entity_id = entry.entity_id
        return entry.entity_id

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

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        result["state_entity_id"] = self._state_entity_id()
        return result

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self.hass.states.async_remove(self._state_entity_id())


async def async_get_quiet_hours(hass: HomeAssistant) -> QuietHoursManager:
    """Return the initialized integration-global Quiet Hours manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_RUNTIME_KEY)
    if not isinstance(manager, QuietHoursManager):
        manager = QuietHoursManager(hass)
        domain_data[_RUNTIME_KEY] = manager
    await manager.async_setup()
    return manager
