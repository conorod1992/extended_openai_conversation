"""Global Quiet Hours policy for Assist satellites and related device controls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_STORAGE_VERSION = 2
_STORAGE_KEY = f"{DOMAIN}.quiet_hours"
_RUNTIME_KEY = "quiet_hours_manager"
_VOLUME_TOLERANCE = 0.005
_RECONCILE_INTERVAL = timedelta(minutes=5)

DEFAULT_START = "22:00"
DEFAULT_END = "07:00"
DEFAULT_VOLUME = 0.20
DEFAULT_WAKE_SOUND_ENABLED = False


@dataclass(frozen=True, slots=True)
class SatelliteOverride:
    """Optional manual entity mappings for one Assist satellite."""

    satellite_entity_id: str
    media_player_entity_id: str | None = None
    wake_sound_entity_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "media_player_entity_id": self.media_player_entity_id,
            "wake_sound_entity_id": self.wake_sound_entity_id,
        }


@dataclass(frozen=True, slots=True)
class QuietHoursConfig:
    """Global Quiet Hours configuration."""

    enabled: bool = False
    start: str = DEFAULT_START
    end: str = DEFAULT_END
    volume_level: float = DEFAULT_VOLUME
    wake_sound_enabled: bool = DEFAULT_WAKE_SOUND_ENABLED
    overrides: tuple[SatelliteOverride, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "start": self.start,
            "end": self.end,
            "volume_level": self.volume_level,
            "wake_sound_enabled": self.wake_sound_enabled,
            "overrides": {
                override.satellite_entity_id: override.as_dict()
                for override in self.overrides
            },
        }

    def override_for(self, satellite_entity_id: str) -> SatelliteOverride | None:
        return next(
            (
                override
                for override in self.overrides
                if override.satellite_entity_id == satellite_entity_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class QuietPeriod:
    """Concrete occurrence of the configured daily Quiet Hours window."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class SatelliteCapabilities:
    """Controls discovered for one Assist satellite."""

    satellite_entity_id: str
    name: str
    device_id: str | None
    media_player_entity_id: str | None
    media_player_source: str | None
    wake_sound_entity_id: str | None
    wake_sound_source: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "satellite_entity_id": self.satellite_entity_id,
            "name": self.name,
            "device_id": self.device_id,
            "media_player_entity_id": self.media_player_entity_id,
            "media_player_source": self.media_player_source,
            "wake_sound_entity_id": self.wake_sound_entity_id,
            "wake_sound_source": self.wake_sound_source,
        }


def _parse_clock(value: str) -> time:
    if not isinstance(value, str):
        raise ValueError("time must be an HH:MM string")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as err:
        raise ValueError("time must use HH:MM") from err
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("time must use HH:MM")
    return parsed.replace(second=0, microsecond=0)


def _normalize_clock(value: str) -> str:
    return _parse_clock(value).strftime("%H:%M")


def quiet_period_for(
    now: datetime, start_value: str, end_value: str
) -> QuietPeriod | None:
    """Return the active quiet period containing ``now``, if any."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start_time = _parse_clock(start_value)
    end_time = _parse_clock(end_value)
    if start_time == end_time:
        raise ValueError("Quiet Hours start and end times must differ")

    def at(day, clock: time) -> datetime:
        return datetime.combine(day, clock, tzinfo=now.tzinfo)

    if start_time < end_time:
        start = at(now.date(), start_time)
        end = at(now.date(), end_time)
        return QuietPeriod(start, end) if start <= now < end else None

    if now.timetz().replace(tzinfo=None) >= start_time:
        return QuietPeriod(
            at(now.date(), start_time),
            at(now.date() + timedelta(days=1), end_time),
        )
    if now.timetz().replace(tzinfo=None) < end_time:
        return QuietPeriod(
            at(now.date() - timedelta(days=1), start_time),
            at(now.date(), end_time),
        )
    return None


def _validate_entity_id(value: Any, domain: str, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.startswith(f"{domain}."):
        raise ValueError(f"{label} must be a {domain} entity")
    return value.strip()


def _config_from_data(value: Any) -> QuietHoursConfig:
    """Validate and normalize persisted or management-provided configuration."""
    if value is None:
        return QuietHoursConfig()
    if not isinstance(value, Mapping):
        raise ValueError("Quiet Hours config must be an object")

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    start = _normalize_clock(value.get("start", DEFAULT_START))
    end = _normalize_clock(value.get("end", DEFAULT_END))
    if start == end:
        raise ValueError("Quiet Hours start and end times must differ")

    volume = value.get("volume_level", DEFAULT_VOLUME)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        raise ValueError("volume_level must be a number")
    volume = float(volume)
    if not math.isfinite(volume) or not 0.0 <= volume <= 1.0:
        raise ValueError("volume_level must be between 0 and 1")

    wake_sound_enabled = value.get(
        "wake_sound_enabled", DEFAULT_WAKE_SOUND_ENABLED
    )
    if not isinstance(wake_sound_enabled, bool):
        raise ValueError("wake_sound_enabled must be a boolean")

    raw_overrides = value.get("overrides", {})
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("overrides must be an object")
    if len(raw_overrides) > 100:
        raise ValueError("Configure no more than 100 satellite overrides")

    overrides: list[SatelliteOverride] = []
    for satellite_entity_id, raw in raw_overrides.items():
        if (
            not isinstance(satellite_entity_id, str)
            or not satellite_entity_id.startswith("assist_satellite.")
        ):
            raise ValueError("Override keys must be assist_satellite entities")
        if not isinstance(raw, Mapping):
            raise ValueError(f"Override for {satellite_entity_id} must be an object")
        overrides.append(
            SatelliteOverride(
                satellite_entity_id=satellite_entity_id.strip(),
                media_player_entity_id=_validate_entity_id(
                    raw.get("media_player_entity_id"),
                    "media_player",
                    "media_player_entity_id",
                ),
                wake_sound_entity_id=_validate_entity_id(
                    raw.get("wake_sound_entity_id"),
                    "switch",
                    "wake_sound_entity_id",
                ),
            )
        )

    return QuietHoursConfig(
        enabled=enabled,
        start=start,
        end=end,
        volume_level=volume,
        wake_sound_enabled=wake_sound_enabled,
        overrides=tuple(overrides),
    )


def _safe_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _current_volume(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    value = state.attributes.get("volume_level")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None


def _current_switch(hass: HomeAssistant, entity_id: str) -> bool | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    if state.state == "on":
        return True
    if state.state == "off":
        return False
    return None


def _entry_name(entry: er.RegistryEntry) -> str:
    return (entry.original_name or entry.name or "").strip().casefold()


def _pick_media_player(
    hass: HomeAssistant, entries: list[er.RegistryEntry]
) -> str | None:
    candidates = [
        entry
        for entry in entries
        if entry.domain == "media_player" and entry.disabled_by is None
    ]
    if not candidates:
        return None

    def score(entry: er.RegistryEntry) -> tuple[int, str]:
        state = hass.states.get(entry.entity_id)
        has_volume = bool(
            state is not None and isinstance(state.attributes.get("volume_level"), (int, float))
        )
        name = _entry_name(entry)
        return (
            (8 if has_volume else 0)
            + (4 if entry.platform == "esphome" else 0)
            + (2 if name == "media player" else 0)
            + (1 if "media" in name else 0),
            entry.entity_id,
        )

    return max(candidates, key=score).entity_id


def _pick_wake_sound(entries: list[er.RegistryEntry]) -> str | None:
    candidates = [
        entry
        for entry in entries
        if entry.domain == "switch"
        and entry.disabled_by is None
        and _entry_name(entry) in {"wake sound", "wake word sound"}
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda entry: (entry.platform == "esphome", entry.entity_id), reverse=True
    )
    return candidates[0].entity_id


def discover_satellite_capabilities(
    hass: HomeAssistant, config: QuietHoursConfig
) -> list[SatelliteCapabilities]:
    """Discover controllable entities attached to every Assist satellite device."""
    registry = er.async_get(hass)
    entries_by_device: dict[str, list[er.RegistryEntry]] = {}
    for entry in registry.entities.values():
        if entry.device_id is not None:
            entries_by_device.setdefault(entry.device_id, []).append(entry)

    satellites: list[SatelliteCapabilities] = []
    for state in hass.states.async_all("assist_satellite"):
        satellite_entry = registry.async_get(state.entity_id)
        device_id = satellite_entry.device_id if satellite_entry is not None else None
        device_entries = entries_by_device.get(device_id, []) if device_id else []
        override = config.override_for(state.entity_id)

        auto_media = _pick_media_player(hass, device_entries) if device_id else None
        auto_wake = _pick_wake_sound(device_entries) if device_id else None
        media = override.media_player_entity_id if override and override.media_player_entity_id else auto_media
        wake = override.wake_sound_entity_id if override and override.wake_sound_entity_id else auto_wake

        satellites.append(
            SatelliteCapabilities(
                satellite_entity_id=state.entity_id,
                name=state.attributes.get("friendly_name", state.entity_id),
                device_id=device_id,
                media_player_entity_id=media,
                media_player_source=(
                    "manual"
                    if override and override.media_player_entity_id
                    else "auto" if auto_media else None
                ),
                wake_sound_entity_id=wake,
                wake_sound_source=(
                    "manual"
                    if override and override.wake_sound_entity_id
                    else "auto" if auto_wake else None
                ),
            )
        )
    return sorted(satellites, key=lambda item: item.name.casefold())


class QuietHoursManager:
    """Apply and safely restore Quiet Hours policy across Assist satellites."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        self._config = QuietHoursConfig()
        self._active: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._unsubscribers: list[Callable[[], None]] = []

    @property
    def config(self) -> QuietHoursConfig:
        return self._config

    @property
    def active(self) -> dict[str, Any] | None:
        if self._active is None:
            return None
        return {
            **self._active,
            "controls": {
                key: dict(value)
                for key, value in self._active.get("controls", {}).items()
                if isinstance(value, Mapping)
            },
        }

    async def async_setup(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            raw = await self._store.async_load() or {}
            try:
                self._config = _config_from_data(raw.get("config"))
            except ValueError:
                self._config = QuietHoursConfig()
            self._active = self._normalize_active(raw.get("active"))
            self._initialized = True
            self._reschedule()
        await self.async_reconcile()

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def discovery_snapshot(self) -> list[dict[str, Any]]:
        return [
            capability.as_dict()
            for capability in discover_satellite_capabilities(self.hass, self._config)
        ]

    def snapshot(self) -> dict[str, Any]:
        now = dt_util.now()
        period = (
            quiet_period_for(now, self._config.start, self._config.end)
            if self._config.enabled
            else None
        )
        return {
            "config": self._config.as_dict(),
            "active": period is not None,
            "period_started_at": period.start.isoformat() if period else None,
            "period_ends_at": period.end.isoformat() if period else None,
            "owned_controls": sorted((self._active or {}).get("controls", {})),
            "satellites": self.discovery_snapshot(),
        }

    async def async_update_config(self, value: Any) -> dict[str, Any]:
        candidate = _config_from_data(value)
        async with self._lock:
            await self._async_restore_locked()
            previous_config = self._config
            self._config = candidate
            try:
                await self._async_save_locked()
            except BaseException:
                self._config = previous_config
                raise
            self._reschedule()
        await self.async_reconcile()
        return self.snapshot()

    async def async_reconcile(self, *, now: datetime | None = None) -> None:
        """Reconcile current devices without reclaiming manually changed controls."""
        now = now or dt_util.now()
        async with self._lock:
            if not self._initialized:
                return
            period = (
                quiet_period_for(now, self._config.start, self._config.end)
                if self._config.enabled
                else None
            )
            if period is None:
                await self._async_restore_locked()
                return

            period_id = period.start.isoformat()
            if self._active is not None and self._active.get("period_started_at") != period_id:
                # Never project ownership from an old occurrence into a later one.
                self._active = None
                await self._async_save_locked()

            if self._active is None:
                self._active = {
                    "period_started_at": period_id,
                    "period_ends_at": period.end.isoformat(),
                    "applied_at": now.isoformat(),
                    "controls": {},
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
                if capability.wake_sound_entity_id:
                    await self._async_apply_switch_locked(
                        capability.satellite_entity_id,
                        capability.wake_sound_entity_id,
                        controls,
                    )

    async def _async_apply_volume_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        controls: dict[str, Any],
    ) -> None:
        if entity_id in controls:
            return
        original = _current_volume(self.hass, entity_id)
        if original is None:
            return
        controls[entity_id] = {
            "kind": "volume",
            "satellite_entity_id": satellite_entity_id,
            "original_value": original,
            "quiet_value": self._config.volume_level,
        }
        await self._async_save_locked()
        try:
            await self._async_set_volume(entity_id, self._config.volume_level)
        except Exception:
            controls.pop(entity_id, None)
            await self._async_save_locked()

    async def _async_apply_switch_locked(
        self,
        satellite_entity_id: str,
        entity_id: str,
        controls: dict[str, Any],
    ) -> None:
        if entity_id in controls:
            return
        original = _current_switch(self.hass, entity_id)
        if original is None:
            return
        controls[entity_id] = {
            "kind": "switch",
            "satellite_entity_id": satellite_entity_id,
            "original_value": original,
            "quiet_value": self._config.wake_sound_enabled,
        }
        await self._async_save_locked()
        try:
            await self._async_set_switch(entity_id, self._config.wake_sound_enabled)
        except Exception:
            controls.pop(entity_id, None)
            await self._async_save_locked()

    async def _async_restore_locked(self) -> None:
        if self._active is None:
            return
        controls = self._active.get("controls", {})
        if isinstance(controls, Mapping):
            for entity_id, raw in list(controls.items()):
                if not isinstance(entity_id, str) or not isinstance(raw, Mapping):
                    continue
                kind = raw.get("kind")
                original = raw.get("original_value")
                quiet = raw.get("quiet_value")
                try:
                    if kind == "volume" and isinstance(original, (int, float)) and isinstance(quiet, (int, float)):
                        current = _current_volume(self.hass, entity_id)
                        if current is not None and math.isclose(current, float(quiet), abs_tol=_VOLUME_TOLERANCE):
                            await self._async_set_volume(entity_id, float(original))
                    elif kind == "switch" and isinstance(original, bool) and isinstance(quiet, bool):
                        current_switch = _current_switch(self.hass, entity_id)
                        if current_switch is quiet:
                            await self._async_set_switch(entity_id, original)
                except Exception:
                    # Do not keep claiming ownership after a failed restoration.
                    pass
        self._active = None
        await self._async_save_locked()

    async def _async_set_volume(self, entity_id: str, volume_level: float) -> None:
        await self.hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": volume_level},
            blocking=True,
        )

    async def _async_set_switch(self, entity_id: str, enabled: bool) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_on" if enabled else "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _async_save_locked(self) -> None:
        await self._store.async_save(
            {"config": self._config.as_dict(), "active": self._active}
        )

    def _normalize_active(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        started = _safe_iso(value.get("period_started_at"))
        ended = _safe_iso(value.get("period_ends_at"))
        if started is None or ended is None or ended <= started:
            return None
        raw_controls = value.get("controls")
        if not isinstance(raw_controls, Mapping):
            return None
        controls: dict[str, dict[str, Any]] = {}
        for entity_id, raw in raw_controls.items():
            if not isinstance(entity_id, str) or not isinstance(raw, Mapping):
                continue
            kind = raw.get("kind")
            original = raw.get("original_value")
            quiet = raw.get("quiet_value")
            if kind == "volume" and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in (original, quiet)
            ):
                if 0.0 <= float(original) <= 1.0 and 0.0 <= float(quiet) <= 1.0:
                    controls[entity_id] = {
                        "kind": kind,
                        "satellite_entity_id": raw.get("satellite_entity_id"),
                        "original_value": float(original),
                        "quiet_value": float(quiet),
                    }
            elif kind == "switch" and isinstance(original, bool) and isinstance(quiet, bool):
                controls[entity_id] = {
                    "kind": kind,
                    "satellite_entity_id": raw.get("satellite_entity_id"),
                    "original_value": original,
                    "quiet_value": quiet,
                }
        return {
            "period_started_at": started.isoformat(),
            "period_ends_at": ended.isoformat(),
            "applied_at": value.get("applied_at"),
            "controls": controls,
        }

    def _reschedule(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if not self._config.enabled:
            return
        for value in (self._config.start, self._config.end):
            clock = _parse_clock(value)
            self._unsubscribers.append(
                async_track_time_change(
                    self.hass,
                    self._handle_time_transition,
                    hour=clock.hour,
                    minute=clock.minute,
                    second=0,
                )
            )
        # Also retry discovery periodically so a satellite that comes online or is
        # added during the active period can still acquire Quiet Hours policy.
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._handle_time_transition,
                _RECONCILE_INTERVAL,
            )
        )

    async def _handle_time_transition(self, now: datetime) -> None:
        await self.async_reconcile(now=now)


async def async_get_quiet_hours(hass: HomeAssistant) -> QuietHoursManager:
    """Return the initialized domain-global Quiet Hours manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_RUNTIME_KEY)
    if not isinstance(manager, QuietHoursManager):
        manager = QuietHoursManager(hass)
        domain_data[_RUNTIME_KEY] = manager
    await manager.async_setup()
    return manager
