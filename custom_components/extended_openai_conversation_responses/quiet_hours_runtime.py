"""Global Quiet Hours policy for Assist satellites.

Quiet Hours intentionally owns only two common satellite controls: an output
volume ceiling and, where available, the wake acknowledgement sound.  Other
satellite-specific controls belong in normal Home Assistant automations, which
can use the integration-global Quiet Hours binary-sensor state as their trigger.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import logging
import math
from typing import Any, cast

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .operational_errors import log_handled_failure
from .persistence_hardening import _async_settle_transactional_save
from .strict_store import PropagatingWriteStore, _async_settle_store_io

_STORAGE_VERSION = 2
_STORAGE_KEY = f"{DOMAIN}.quiet_hours"
_RUNTIME_KEY = "quiet_hours_manager"
_STATE_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
_VOLUME_TOLERANCE = 0.005
_DISCOVERY_INTERVAL = timedelta(minutes=5)

DEFAULT_START = "22:00"
DEFAULT_END = "07:00"
DEFAULT_MAX_VOLUME = 0.2
DEFAULT_WAKE_SOUND = "off"
_WAKE_SOUND_VALUES = {"off", "on", "unchanged"}


@dataclass(frozen=True, slots=True)
class SatelliteOverride:
    """Manual entity overrides for one Assist satellite."""

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
    """Integration-global Quiet Hours configuration."""

    enabled: bool = False
    start: str = DEFAULT_START
    end: str = DEFAULT_END
    max_volume: float = DEFAULT_MAX_VOLUME
    wake_sound: str = DEFAULT_WAKE_SOUND
    overrides: tuple[SatelliteOverride, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "start": self.start,
            "end": self.end,
            "max_volume": self.max_volume,
            "wake_sound": self.wake_sound,
            "overrides": {
                override.satellite_entity_id: override.as_dict()
                for override in self.overrides
            },
        }


@dataclass(frozen=True, slots=True)
class QuietPeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class SatelliteCapabilities:
    """Controls discovered for one Assist satellite."""

    satellite_entity_id: str
    name: str
    device_id: str | None
    media_player_entity_id: str | None
    wake_sound_entity_id: str | None
    media_player_source: str | None
    wake_sound_source: str | None
    media_player_candidates: tuple[str, ...] = ()
    wake_sound_candidates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "satellite_entity_id": self.satellite_entity_id,
            "name": self.name,
            "device_id": self.device_id,
            "media_player_entity_id": self.media_player_entity_id,
            "wake_sound_entity_id": self.wake_sound_entity_id,
            "media_player_source": self.media_player_source,
            "wake_sound_source": self.wake_sound_source,
            "media_player_candidates": list(self.media_player_candidates),
            "wake_sound_candidates": list(self.wake_sound_candidates),
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
    """Return the active schedule occurrence containing ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start_time = _parse_clock(start_value)
    end_time = _parse_clock(end_value)
    if start_time == end_time:
        raise ValueError("Quiet Hours start and end times must differ")

    def at(day: date, clock: time, *, ending: bool = False) -> datetime:
        candidate = datetime.combine(day, clock, tzinfo=now.tzinfo)
        # Preserve spring-gap normalization. For a repeated hour, one daily
        # occurrence starts at the first instant and ends at the last instant.
        normalized = candidate.astimezone(UTC).astimezone(now.tzinfo)
        if normalized.replace(fold=0) != candidate.replace(fold=0):
            return normalized
        if ending:
            second = candidate.replace(fold=1)
            if second.astimezone(UTC).astimezone(now.tzinfo) == second:
                return max((candidate, second), key=lambda value: value.astimezone(UTC))
        return candidate

    instant = now.astimezone(UTC)
    for day in (now.date(), now.date() - timedelta(days=1)):
        start = at(day, start_time)
        end = at(
            day + timedelta(days=int(start_time > end_time)), end_time, ending=True
        )
        if start.astimezone(UTC) <= instant < end.astimezone(UTC):
            return QuietPeriod(start, end)
    return None


def _optional_entity(value: Any, domain: str, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.startswith(f"{domain}."):
        raise ValueError(f"{label} must be a {domain} entity")
    return value.strip()


def _config_from_data(value: Any) -> QuietHoursConfig:
    """Validate current config and migrate the earlier branch representation."""
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

    # Earlier experimental builds called this volume_level.  Accept it so a
    # stored prototype config does not become invalid after the rename.
    volume = value.get("max_volume", value.get("volume_level", DEFAULT_MAX_VOLUME))
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        raise ValueError("max_volume must be a number")
    volume = float(volume)
    if not math.isfinite(volume) or not 0.0 <= volume <= 1.0:
        raise ValueError("max_volume must be between 0 and 1")

    wake_sound = value.get("wake_sound")
    if wake_sound is None and "wake_sound_enabled" in value:
        legacy = value.get("wake_sound_enabled")
        if not isinstance(legacy, bool):
            raise ValueError("wake_sound_enabled must be a boolean")
        wake_sound = "on" if legacy else "off"
    if wake_sound is None:
        wake_sound = DEFAULT_WAKE_SOUND
    if not isinstance(wake_sound, str) or wake_sound not in _WAKE_SOUND_VALUES:
        raise ValueError("wake_sound must be on, off, or unchanged")

    raw_overrides = value.get("overrides", {})
    if isinstance(raw_overrides, list):
        raw_overrides = {
            item.get("satellite_entity_id"): item
            for item in raw_overrides
            if isinstance(item, Mapping) and item.get("satellite_entity_id")
        }
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("overrides must be an object")
    if len(raw_overrides) > 100:
        raise ValueError("Select no more than 100 satellite overrides")

    overrides: list[SatelliteOverride] = []
    for satellite_entity_id, raw in raw_overrides.items():
        if not isinstance(
            satellite_entity_id, str
        ) or not satellite_entity_id.startswith("assist_satellite."):
            raise ValueError("Override keys must be assist_satellite entities")
        if not isinstance(raw, Mapping):
            raise ValueError(f"Override for {satellite_entity_id} must be an object")
        overrides.append(
            SatelliteOverride(
                satellite_entity_id=satellite_entity_id,
                media_player_entity_id=_optional_entity(
                    raw.get("media_player_entity_id"), "media_player", "Volume override"
                ),
                wake_sound_entity_id=_optional_entity(
                    raw.get("wake_sound_entity_id"), "switch", "Wake sound override"
                ),
            )
        )

    return QuietHoursConfig(
        enabled=enabled,
        start=start,
        end=end,
        max_volume=volume,
        wake_sound=wake_sound,
        overrides=tuple(overrides),
    )


def _safe_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def _current_volume(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or getattr(state, "state", None) in {"unknown", "unavailable"}:
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
    return " ".join(
        str(value)
        for value in (entry.name, entry.original_name, entry.entity_id)
        if value
    ).lower()


def _override_map(config: QuietHoursConfig) -> dict[str, SatelliteOverride]:
    return {item.satellite_entity_id: item for item in config.overrides}


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
            state is not None
            and isinstance(state.attributes.get("volume_level"), (int, float))
            and not isinstance(state.attributes.get("volume_level"), bool)
        )
        name = _entry_name(entry)
        return (
            (8 if has_volume else 0)
            + (4 if entry.platform == "esphome" else 0)
            + (2 if "media player" in name or "speaker" in name else 0),
            entry.entity_id,
        )

    return max(candidates, key=score).entity_id


def _pick_wake_sound(entries: list[er.RegistryEntry]) -> str | None:
    candidates = [
        entry
        for entry in entries
        if entry.domain == "switch"
        and entry.disabled_by is None
        and "wake sound" in _entry_name(entry)
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


class QuietHoursManager:
    """Apply a small global Quiet Hours policy without becoming an automation engine."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = PropagatingWriteStore(
            hass, _STORAGE_VERSION, _STORAGE_KEY
        )
        self._config = QuietHoursConfig()
        self._active: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._stopping = False
        self._config_recovery_pending = False
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
            if getattr(self, "_stopping", False):
                return
            if self._config_recovery_pending:
                if not await self._async_recover_configuration_locked():
                    return
            elif self._initialized:
                return
            else:
                raw = await _async_settle_store_io(self._store.async_load()) or {}
                try:
                    self._config = _config_from_data(raw.get("config"))
                except ValueError:
                    self._config = QuietHoursConfig()
                self._active = self._normalize_active(raw.get("active"))
                self._config_recovery_pending = False
                self._initialized = True
                self._reschedule()
        await self.async_reconcile()

    async def async_shutdown(self) -> None:
        # Unsubscribing cannot retract callbacks already queued on this lock.
        self._stopping = True
        async with self._lock:
            self._cancel_scheduled_callbacks()
            try:
                await self._async_restore_locked()
            finally:
                # Recovery can rearm an authoritative generation while stopping.
                self._cancel_scheduled_callbacks()
                self._unpublish_state()

    def _discover_capabilities(self) -> list[SatelliteCapabilities]:
        return discover_satellite_capabilities(self.hass, self._config)

    def discovery_snapshot(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._discover_capabilities()]

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
            "state_entity_id": _STATE_ENTITY_ID,
            "satellites": self.discovery_snapshot(),
        }

    async def async_set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable the daily schedule without changing its policy."""
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        config = self.config.as_dict()
        config["enabled"] = enabled
        return await self.async_update_config(config)

    async def async_update_config(self, value: Any) -> dict[str, Any]:
        candidate = _config_from_data(value)
        failure: BaseException | None = None
        async with self._lock:
            if getattr(self, "_stopping", False):
                raise HomeAssistantError("Quiet Hours is shutting down")
            if not await self._async_recover_configuration_locked():
                raise ValueError(
                    "Quiet Hours configuration recovery is pending; retry later"
                )
            previous = self._config
            self._config = candidate
            try:
                await self._async_save_locked()
                now = dt_util.now()
                old_period = (
                    quiet_period_for(now, previous.start, previous.end)
                    if previous.enabled
                    else None
                )
                new_period = (
                    quiet_period_for(now, candidate.start, candidate.end)
                    if candidate.enabled
                    else None
                )
                if (
                    self._active is not None
                    and old_period is not None
                    and new_period is not None
                ):
                    self._active.update(
                        period_started_at=new_period.start.isoformat(),
                        period_ends_at=new_period.end.isoformat(),
                    )
                    self._active["observed_controls"] = list(
                        self._active.get("controls", {})
                    )
                    if "observed_identities" in self._active:
                        self._active["observed_identities"] = {
                            key: identity
                            for key, identity in self._active[
                                "observed_identities"
                            ].items()
                            if key in self._active["controls"]
                        }
                    self._active["pending_controls"] = {}
                    await self._async_save_control_state_locked()
            except BaseException as err:
                failure = err
                if self._config_recovery_pending:
                    # Publish no speculative configuration while disk is unknown.
                    self._config = previous
            needs_reconcile = failure is None or self._config != previous
            needs_schedule = needs_reconcile or self._config_recovery_pending
        try:
            if needs_reconcile and not self._config_recovery_pending:
                await self.async_reconcile()
        except BaseException as recovery_error:
            if failure is not None:
                raise failure from recovery_error
            raise
        finally:
            if needs_schedule:
                async with self._lock:
                    self._reschedule()
        if failure is not None:
            raise failure
        return self.snapshot()

    async def _async_recover_configuration_locked(self) -> bool:
        """Suspend device changes until an ambiguous config write is read back."""
        if not getattr(self, "_config_recovery_pending", False):
            self._config_recovery_pending = False
            return True
        try:
            await _async_settle_store_io(self._async_reload_generation_locked())
        except Exception:
            return False
        return True

    async def _async_reload_generation_locked(self) -> None:
        """Validate both halves before installing an authoritative generation."""
        recovering = (
            getattr(self, "_config_recovery_pending", False) or not self._initialized
        )
        stored = await self._store.async_load()
        if stored is not None and not isinstance(stored, Mapping):
            raise ValueError("Quiet Hours configuration storage is invalid")
        if (self._active or {}).get("controls") and (
            stored is None or "config" not in stored or "active" not in stored
        ):
            raise ValueError("Quiet Hours saved ownership is unavailable")
        raw = stored or {}
        config = _config_from_data(raw.get("config"))
        active = self._normalize_active(raw.get("active"))
        if raw.get("active") is not None and active is None:
            raise ValueError("Quiet Hours saved ownership is invalid")
        self._config = config
        self._active = active
        self._mark_generation_available()
        # Installing a readback generation must also secure its callbacks before
        # deferred caller cancellation can leave the recovery operation.
        if recovering:
            self._reschedule()

    def _mark_generation_available(self) -> None:
        self._config_recovery_pending = False
        self._initialized = True

    def _invalidate_generation(self) -> None:
        # Keep unresolved ownership for diagnostics and retry; forbid actions
        # until authoritative readback succeeds, including while disabled.
        self._config_recovery_pending = True
        self._initialized = False
        self._reschedule()

    def _active_period_overlaps(self, period: QuietPeriod) -> bool:
        """Preserve ownership across a continuously quiet schedule change."""
        if self._active is None:
            return False
        start = _safe_iso(self._active.get("period_started_at"))
        end = _safe_iso(self._active.get("period_ends_at"))
        return (
            start is not None
            and end is not None
            and start <= period.end
            and period.start <= end
        )

    async def async_reconcile(self, *, now: datetime | None = None) -> None:
        """Reconcile the scheduled policy using cross-version-safe discovery."""
        now = dt_util.now() if now is None else dt_util.as_local(now)
        async with self._lock:
            if getattr(self, "_stopping", False):
                return
            recovering = getattr(self, "_config_recovery_pending", False)
            if not self._initialized and not recovering:
                return
            if not await self._async_recover_configuration_locked():
                return
            await self._async_resolve_control_identities_locked()
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
                if not self._active_period_overlaps(period):
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
            capabilities = self._discover_capabilities()
            await self._async_reconcile_controls_locked(capabilities)
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
            for capability in capabilities:
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

    async def _async_resolve_control_identities_locked(self) -> None:
        if self._migrate_control_entity_ids():
            await self._async_save_control_state_locked()

    def _migrate_control_entity_ids(self) -> bool:
        """Follow registry identities when owned speakers or switches are renamed."""
        if self._active is None:
            return False
        destinations: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        moves: dict[str, str | None] = {}
        for key in ("controls", "pending_controls"):
            controls = self._active.get(key, {})
            if not isinstance(controls, Mapping):
                continue
            for old_id, control in controls.items():
                if not isinstance(old_id, str) or not isinstance(control, dict):
                    continue
                identity = control.get("registry_identity")
                new_id = (
                    er.async_get(self.hass).async_get_entity_id(*identity)
                    if isinstance(identity, list) and len(identity) == 3
                    else old_id
                )
                moves[old_id] = new_id if isinstance(new_id, str) else None
                if not isinstance(new_id, str):
                    continue
                destinations.setdefault(new_id, []).append((key, old_id, control))
        rebuilt: dict[str, dict[str, Any]] = {"controls": {}, "pending_controls": {}}
        ambiguous = []
        for destination, records in destinations.items():
            # Resolve the entire mapping before moving anything: swaps and rename
            # chains must carry each identity's baseline to its current ID.
            # A legacy ID alone cannot establish ownership against a record
            # whose stable registry identity resolves to this current device.
            verified = [
                record
                for record in records
                if isinstance(record[2].get("registry_identity"), list)
                and len(record[2]["registry_identity"]) == 3
            ]
            candidates = verified or records
            # Among those candidates, pending intents cannot displace
            # an applied (or possibly applied prepared) control's restoration
            # obligation merely because their old key matches the destination.
            owned = [record for record in candidates if record[0] == "controls"]
            candidates = owned or candidates
            anchored = [record for record in candidates if record[1] == destination]
            selected = (
                anchored[:1] if anchored else candidates if len(candidates) == 1 else []
            )
            if not selected:
                ambiguous.append(destination)
            for key, _old_id, control in selected:
                rebuilt[key][destination] = control
            # A collision without a canonical destination owner is ambiguous.
            # Release its claims, retaining observation to avoid reclaiming it.
        observed = []
        identities = {}
        previous_identities = self._active.get("observed_identities", {})
        for old_id in self._active.get("observed_controls", []):
            identity = previous_identities.get(old_id)
            if identity is None:
                for key in ("controls", "pending_controls"):
                    identity = (
                        self._active.get(key, {})
                        .get(old_id, {})
                        .get("registry_identity")
                    )
                    if identity is not None:
                        break
            verified = isinstance(identity, list) and len(identity) == 3
            destination = (
                er.async_get(self.hass).async_get_entity_id(*identity)
                if verified
                else moves.get(old_id, old_id)
            )
            if not isinstance(destination, str):
                continue
            observed.append(destination)
            if verified:
                # Applied ownership wins a metadata collision too. Observation
                # must never change which baseline owns the current device.
                owner = rebuilt["controls"].get(destination, {})
                identities[destination] = owner.get("registry_identity", identity)
        observed = list(dict.fromkeys(observed + ambiguous))
        changed = (
            any(self._active.get(key, {}) != value for key, value in rebuilt.items())
            or self._active.get("observed_controls", []) != observed
            or previous_identities != identities
        )
        if changed:
            self._active.update(rebuilt, observed_controls=observed)
            if identities or "observed_identities" in self._active:
                self._active["observed_identities"] = identities
        return changed

    async def _async_reconcile_controls_locked(
        self, capabilities: list[SatelliteCapabilities]
    ) -> None:
        """Retarget retained ownership directly and restore only departing controls."""
        if self._active is None:
            return
        desired = {}
        for item in capabilities:
            if item.media_player_entity_id:
                desired[item.media_player_entity_id] = (
                    "volume",
                    item.satellite_entity_id,
                    self._config.max_volume,
                )
            if item.wake_sound_entity_id and self._config.wake_sound != "unchanged":
                desired[item.wake_sound_entity_id] = (
                    "switch",
                    item.satellite_entity_id,
                    self._config.wake_sound == "on",
                )
        controls = self._active.get("controls", {})
        for entity_id, control in list(controls.items()):
            target = desired.get(entity_id)
            if target is None or target[0] != control["kind"]:
                control["restoration_pending"] = True
                continue
            # Observe ownership before any persistence yields to another actor.
            state = self.hass.states.get(entity_id)
            observed_context = getattr(getattr(state, "context", None), "id", None)
            if control["satellite_entity_id"] != target[1]:
                # Ownership follows the physical control, even if its satellite changes.
                control["satellite_entity_id"] = target[1]
                await self._async_save_control_state_locked()
            if control.get("application_state") == "prepared":
                state = self.hass.states.get(entity_id)
                context_id = control.get("application_context_id")
                attained = (
                    _current_volume(self.hass, entity_id)
                    if target[0] == "volume"
                    else _current_switch(self.hass, entity_id)
                )
                at_quiet_value = (
                    attained is not None
                    and math.isclose(
                        attained, control["quiet_value"], abs_tol=_VOLUME_TOLERANCE
                    )
                    if target[0] == "volume"
                    else attained is control["quiet_value"]
                )
                if not (
                    at_quiet_value
                    and context_id
                    and state
                    and state.context.id == context_id
                ):
                    # The public apply path revalidates an unapplied baseline and intent.
                    continue
                control["application_state"] = "applied"
                await self._async_save_control_state_locked()
            if self._pending_quiet_attained(entity_id, control):
                control["quiet_value"] = control.pop("pending_quiet_value")
                await self._async_save_control_state_locked()
            kind, _, value = target
            if kind == "volume":
                value = min(value, control["original_value"])
            if value == control["quiet_value"] and "pending_quiet_value" not in control:
                control.pop("restoration_pending", None)
                continue
            current = (
                _current_volume(self.hass, entity_id)
                if kind == "volume"
                else _current_switch(self.hass, entity_id)
            )
            if current is None:
                continue
            same = (
                math.isclose(current, control["quiet_value"], abs_tol=_VOLUME_TOLERANCE)
                if kind == "volume"
                else current is control["quiet_value"]
            )
            if not same:
                # An independent adjustment ends ownership; never claim its value as our baseline.
                controls.pop(entity_id)
                await self._async_save_control_state_locked()
                continue
            control["pending_quiet_value"] = value
            control.pop("restoration_pending", None)
            await self._async_save_control_state_locked()
            # Saving can yield to a manual adjustment or an unavailable device.
            current = (
                _current_volume(self.hass, entity_id)
                if kind == "volume"
                else _current_switch(self.hass, entity_id)
            )
            if current is None:
                continue
            state = self.hass.states.get(entity_id)
            current_context = getattr(getattr(state, "context", None), "id", None)
            same = (
                math.isclose(current, control["quiet_value"], abs_tol=_VOLUME_TOLERANCE)
                if kind == "volume"
                else current is control["quiet_value"]
            )
            if not same or current_context != observed_context:
                controls.pop(entity_id)
                await self._async_save_control_state_locked()
                continue
            try:
                if kind == "volume":
                    await self._async_set_volume(entity_id, value)
                else:
                    await self._async_set_switch(entity_id, bool(value))
            except Exception as err:
                self._log_control_failure(
                    "retarget", entity_id, kind, err, ownership="retained"
                )
                continue
            control["quiet_value"] = control.pop("pending_quiet_value")
            await self._async_save_control_state_locked()

    def _pending_quiet_attained(self, entity_id: str, control: dict[str, Any]) -> bool:
        if "pending_quiet_value" not in control:
            return False
        current = (
            _current_volume(self.hass, entity_id)
            if control["kind"] == "volume"
            else _current_switch(self.hass, entity_id)
        )
        desired = control["pending_quiet_value"]
        same = current is not None and (
            math.isclose(current, desired, abs_tol=_VOLUME_TOLERANCE)
            if control["kind"] == "volume"
            else current is desired
        )
        context_id = control.get("application_context_id")
        state = self.hass.states.get(entity_id)
        return same and (
            not context_id
            or getattr(getattr(state, "context", None), "id", None) == context_id
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
            if not needs_action:
                controls.pop(entity_id)
                await self._async_save_control_state_locked()
                return None
            if existing["quiet_value"] != desired:
                existing["quiet_value"] = desired
                await self._async_save_control_state_locked()
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
        if identity := intent.get("registry_identity"):
            self._active.setdefault("observed_identities", {})[entity_id] = list(
                identity
            )
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

    async def _async_restore_locked(self, *, pending_only: bool = False) -> None:
        if not await self._async_recover_configuration_locked():
            return
        await self._async_resolve_control_identities_locked()
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
        if self._active is None:
            return
        controls = self._active.get("controls", {})
        if not isinstance(controls, dict):
            controls = {}
        if isinstance(controls, dict):
            for entity_id, raw in list(controls.items()):
                if not isinstance(entity_id, str) or not isinstance(raw, dict):
                    controls.pop(entity_id)
                    continue
                if pending_only and not raw.get("restoration_pending"):
                    continue
                if self._pending_quiet_attained(entity_id, raw):
                    raw["quiet_value"] = raw.pop("pending_quiet_value")
                kind = raw.get("kind")
                original = raw.get("original_value")
                quiet = raw.get("quiet_value")
                raw["restoration_pending"] = True
                try:
                    if (
                        kind == "volume"
                        and isinstance(original, (int, float))
                        and not isinstance(original, bool)
                        and isinstance(quiet, (int, float))
                        and not isinstance(quiet, bool)
                    ):
                        current = _current_volume(self.hass, entity_id)
                        if current is None:
                            continue
                        if math.isclose(
                            current, float(quiet), abs_tol=_VOLUME_TOLERANCE
                        ) and not math.isclose(
                            current, float(original), abs_tol=_VOLUME_TOLERANCE
                        ):
                            await self._async_set_volume(entity_id, float(original))
                    elif (
                        kind == "switch"
                        and isinstance(original, bool)
                        and isinstance(quiet, bool)
                    ):
                        current_switch = _current_switch(self.hass, entity_id)
                        if current_switch is None:
                            continue
                        if current_switch is quiet and current_switch is not original:
                            await self._async_set_switch(entity_id, original)
                    # A confirmed independent value releases ownership too.
                    controls.pop(entity_id)
                    if pending_only:
                        observed = self._active.get("observed_controls", [])
                        if entity_id in observed:
                            observed.remove(entity_id)
                except Exception as err:
                    self._log_control_failure(
                        "restore", entity_id, str(kind), err, ownership="retained"
                    )
        if not pending_only:
            if controls:
                self._active["observed_controls"] = list(controls)
                self._active["pending_controls"] = {}
            else:
                self._active = None
        await self._async_save_locked()

    async def _async_retry_pending_restores_locked(self) -> None:
        if any(
            control.get("restoration_pending")
            for control in (self._active or {}).get("controls", {}).values()
        ):
            await self._async_restore_locked(pending_only=True)

    async def _async_set_volume(self, entity_id: str, volume_level: float) -> None:
        await self._async_call_control_service(
            "media_player", "volume_set", entity_id, {"volume_level": volume_level}
        )

    async def _async_set_switch(self, entity_id: str, enabled: bool) -> None:
        await self._async_call_control_service(
            "switch", "turn_on" if enabled else "turn_off", entity_id, {}
        )

    async def _async_call_control_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> None:
        """Associate an observable HA effect with its persisted prepared intent."""
        control = (self._active or {}).get("controls", {}).get(entity_id, {})
        identity = control.get("registry_identity")
        if (
            isinstance(identity, list)
            and len(identity) == 3
            and er.async_get(self.hass).async_get_entity_id(*identity) != entity_id
        ):
            # Migration persistence awaits storage. A rename or ID reuse during
            # that await must not redirect the subsequent action to another owner.
            raise HomeAssistantError(
                "Quiet Hours control identity changed; retry reconciliation"
            )
        context_id = control.get("application_context_id")
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
            context=Context(id=context_id) if context_id else None,
        )

    async def _async_save_control_state_locked(self) -> None:
        await self._async_save_locked()

    async def _async_save_locked(self) -> None:
        await _async_settle_transactional_save(
            self._store.async_save(
                {"config": self._config.as_dict(), "active": self._active}
            ),
            self._invalidate_generation,
            self._mark_generation_available,
            self._async_reload_generation_locked,
            self._invalidate_generation,
            record_execution=False,
        )

    def _normalize_active(self, value: Any) -> dict[str, Any] | None:
        normalized = self._normalize_active_values(value)
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
        pending_state = self._normalize_active_values(
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
        identities = (
            {
                entity_id: list(identity)
                for entity_id, identity in value.get("observed_identities", {}).items()
                if entity_id in observed
                and isinstance(identity, list)
                and len(identity) == 3
                and all(isinstance(item, str) and item for item in identity)
            }
            if isinstance(value.get("observed_identities"), Mapping)
            else {}
        )
        for entity_id, control in {**pending, **normalized["controls"]}.items():
            if identity := control.get("registry_identity"):
                identities[entity_id] = list(identity)
        if identities:
            normalized["observed_identities"] = identities
        return normalized

    def _normalize_active_values(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        started = _safe_iso(value.get("period_started_at"))
        ended = _safe_iso(value.get("period_ends_at"))
        if started is None or ended is None or ended <= started:
            return None
        raw_controls = value.get("controls")
        # Migrate the original volume-only prototype representation.
        if raw_controls is None and isinstance(value.get("targets"), Mapping):
            raw_controls = {
                entity_id: {
                    "kind": "volume",
                    "satellite_entity_id": "",
                    "original_value": raw.get("original_volume"),
                    "quiet_value": raw.get("quiet_volume"),
                }
                for entity_id, raw in value["targets"].items()
                if isinstance(raw, Mapping)
            }
        if not isinstance(raw_controls, Mapping):
            return None
        controls: dict[str, dict[str, Any]] = {}
        for entity_id, raw in raw_controls.items():
            if not isinstance(entity_id, str) or not isinstance(raw, Mapping):
                continue
            kind = raw.get("kind")
            original = raw.get("original_value")
            quiet = raw.get("quiet_value")
            if (
                kind == "volume"
                and isinstance(original, (int, float))
                and not isinstance(original, bool)
                and isinstance(quiet, (int, float))
                and not isinstance(quiet, bool)
                and 0.0 <= float(original) <= 1.0
                and 0.0 <= float(quiet) <= 1.0
            ):
                controls[entity_id] = {
                    "kind": "volume",
                    "satellite_entity_id": str(raw.get("satellite_entity_id") or ""),
                    "original_value": float(original),
                    "quiet_value": float(quiet),
                }
            elif (
                kind == "switch"
                and isinstance(original, bool)
                and isinstance(quiet, bool)
            ):
                controls[entity_id] = {
                    "kind": "switch",
                    "satellite_entity_id": str(raw.get("satellite_entity_id") or ""),
                    "original_value": original,
                    "quiet_value": quiet,
                }
            if entity_id in controls and raw.get("restoration_pending") is True:
                controls[entity_id]["restoration_pending"] = True
            pending_value = raw.get("pending_quiet_value")
            if entity_id in controls and (
                (
                    kind == "volume"
                    and isinstance(pending_value, (int, float))
                    and not isinstance(pending_value, bool)
                    and 0 <= pending_value <= 1
                )
                or (kind == "switch" and isinstance(pending_value, bool))
            ):
                controls[entity_id]["pending_quiet_value"] = pending_value
            identity = raw.get("registry_identity")
            if (
                entity_id in controls
                and isinstance(identity, list)
                and len(identity) == 3
                and all(isinstance(item, str) and item for item in identity)
            ):
                controls[entity_id]["registry_identity"] = list(identity)
        return {
            "period_started_at": started.isoformat(),
            "period_ends_at": ended.isoformat(),
            "applied_at": value.get("applied_at"),
            "controls": controls,
        }

    @staticmethod
    def _normalize_baseline_context(
        control: dict[str, Any], raw: dict[str, Any]
    ) -> None:
        context_id = raw.get("baseline_context_id")
        if context_id is not None:
            if not isinstance(context_id, str) or not context_id:
                raise ValueError("Quiet Hours baseline context is invalid")
            control["baseline_context_id"] = context_id

    def _log_control_failure(
        self,
        operation: str,
        entity_id: str,
        kind: str,
        error: Exception,
        *,
        ownership: str = "released",
    ) -> None:
        log_handled_failure(
            logging.getLogger(__name__),
            f"Quiet Hours operation={operation} control={kind} entity={entity_id}; outcome=failed ownership={ownership}",
            error,
        )

    def _publish_state(self, period: QuietPeriod | None) -> None:
        self.hass.states.async_set(
            _STATE_ENTITY_ID,
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

    def _unpublish_state(self) -> None:
        self.hass.states.async_remove(_STATE_ENTITY_ID)

    def _cancel_scheduled_callbacks(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _reschedule(self) -> None:
        self._cancel_scheduled_callbacks()
        if getattr(self, "_stopping", False):
            return
        if getattr(self, "_config_recovery_pending", False):
            self._unsubscribers.append(
                async_track_time_interval(
                    self.hass, self._handle_discovery_tick, _DISCOVERY_INTERVAL
                )
            )
            return
        # Even when disabled we publish the entity immediately; only scheduled
        # callbacks are unnecessary.
        if not self._config.enabled:
            self._publish_state(None)
            if not (self._active or {}).get("controls"):
                return
        for value in (
            (self._config.start, self._config.end) if self._config.enabled else ()
        ):
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
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._handle_discovery_tick,
                _DISCOVERY_INTERVAL,
            )
        )

    async def _handle_time_transition(self, now: datetime) -> None:
        await self.async_reconcile(now=now)

    async def _handle_discovery_tick(self, now: datetime) -> None:
        await self.async_reconcile(now=now)


async def async_get_quiet_hours(hass: HomeAssistant) -> QuietHoursManager:
    """Return the initialized integration-global manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_RUNTIME_KEY)
    if not isinstance(manager, QuietHoursManager):
        manager = QuietHoursManager(hass)
        domain_data[_RUNTIME_KEY] = manager
    await manager.async_setup()
    return manager


__all__ = [
    "DEFAULT_END",
    "DEFAULT_MAX_VOLUME",
    "DEFAULT_START",
    "DEFAULT_WAKE_SOUND",
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
