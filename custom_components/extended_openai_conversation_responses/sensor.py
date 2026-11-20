"""Diagnostic usage sensors for conversation agents."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
import logging

from homeassistant.components import sensor as sensor_component
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import ExtendedOpenAIConfigEntry
from .agent_maintenance import get_agent_maintenance_gate
from .const import CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL, DOMAIN
from .guest_mode import GuestModeManager, async_get_guest_mode
from .local_time import resolve_wall_time
from .persistence_hardening import StorageRepairRequired
from .usage import UsageManager, async_get_usage, usage_retention_policy

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ExtendedOpenAIConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one compact diagnostic sensor per conversation agent."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        try:
            usage = await async_get_usage(
                hass, config_entry.entry_id, subentry.subentry_id
            )
            guest_mode = await async_get_guest_mode(
                hass, config_entry.entry_id, subentry.subentry_id
            )
        except StorageRepairRequired as err:
            _LOGGER.error(
                "Assistant %s sensors remain unavailable because retained storage requires repair: %s",
                subentry.subentry_id,
                err,
            )
            continue
        except HomeAssistantError:
            gate = get_agent_maintenance_gate(
                hass, config_entry.entry_id, subentry.subentry_id
            )
            if not gate.recovery_required:
                raise
            _LOGGER.error(
                "Assistant %s sensors remain unavailable pending restore recovery",
                subentry.subentry_id,
            )
            continue
        retention = usage_retention_policy(subentry.data)
        usage.request_retention_days = retention["request_retention_days"]
        usage.run_retention_days = retention["run_retention_days"]
        async_add_entities(
            [
                UsageSensor(subentry, usage),
                UsageTodaySensor(subentry, usage),
                UsageMonthSensor(subentry, usage),
                LastResponseUsageSensor(subentry, usage),
                GuestModeSensor(subentry, guest_mode),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class GuestModeSensor(SensorEntity):
    """Expose the integration-owned Guest Mode schedule."""

    _attr_has_entity_name = True
    _attr_translation_key = "guest_mode"
    _attr_icon = "mdi:account-lock"
    _attr_should_poll = False

    def __init__(self, subentry: ConfigSubentry, guest_mode: GuestModeManager) -> None:
        self._guest_mode = guest_mode
        self._attr_unique_id = f"{subentry.subentry_id}_guest_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="OpenAI",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Do not present a speculative schedule after uncertain persistence."""
        return self._guest_mode.authoritative_available

    @property
    def native_value(self) -> str | None:
        if not self.available:
            return None
        return str(self._guest_mode.status()["state"])

    @property
    def extra_state_attributes(self) -> dict:
        if not self.available:
            return {}
        status = self._guest_mode.status()
        return {
            key: status[key]
            for key in (
                "active_from",
                "active_until",
                "indefinite",
                "currently_active",
                "scheduled",
            )
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._guest_mode.async_add_listener(self.async_write_ha_state)
        )


class UsageSensor(SensorEntity):
    """Expose cumulative token usage with request counts as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "usage"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = "tokens"
    _attr_should_poll = False
    # Backup restore can legitimately decrease this counter. A decrease is a
    # correction, not a new meter cycle whose entire value should be added.
    _attr_state_class: SensorStateClass | None = SensorStateClass.TOTAL

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        """Initialize the diagnostic sensor."""
        self._usage = usage
        self._subentry_id = subentry.subentry_id
        self._usage_listener_remove: Callable[[], None] | None = None
        self._attr_unique_id = f"{subentry.subentry_id}_usage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="OpenAI",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int | None:
        """Return cumulative real token usage."""
        return self._usage.totals.total_tokens

    @property
    def available(self) -> bool:
        """Keep incomplete lifetime counters out of Recorder."""
        return self.state_class is None or self._usage.lifetime_available

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the distinct conversation, request, and token counters."""
        return self._usage.as_dict()

    def _remove_usage_listener(self) -> None:
        """Remove the current manager listener without changing entity lifetime."""
        remove = self._usage_listener_remove
        self._usage_listener_remove = None
        if remove is not None:
            remove()

    def rebind_usage(self, usage: UsageManager) -> bool:
        """Move this live sensor to a replacement authoritative Usage manager."""
        if usage is self._usage:
            return False
        subscribed = self._usage_listener_remove is not None
        self._remove_usage_listener()
        self._usage = usage
        if subscribed:
            self._usage_listener_remove = usage.async_add_listener(
                self._handle_usage_update
            )
            self._handle_usage_update()
        return True

    @callback
    def _handle_usage_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to persisted usage updates."""
        await super().async_added_to_hass()
        self._usage_listener_remove = self._usage.async_add_listener(
            self._handle_usage_update
        )
        self.async_on_remove(self._remove_usage_listener)


class _PeriodUsageSensor(UsageSensor):
    """Display a calendar bucket from Usage accounting."""

    _attr_state_class = None

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._cancel_period_rollover: Callable[[], None] | None = None

    def _summary(self) -> dict:
        raise NotImplementedError

    def _period_start(self, today: date) -> date:
        return today

    @property
    def available(self) -> bool:
        """Display the period only when its stored aggregates are trustworthy."""
        return self._usage.daily_available

    @property
    def native_value(self) -> int:
        return int(self._summary()["total_tokens"])

    @property
    def extra_state_attributes(self) -> dict:
        summary = self._summary()
        return {
            "period_start": self._period_start(dt_util.now().date()).isoformat(),
            "input": summary["input_tokens"],
            "output": summary["output_tokens"],
            "cached_input": summary["cached_input_tokens"],
            "reasoning": summary["reasoning_tokens"],
            "runs": summary["run_count"],
            "requests": summary["api_request_count"],
            "failures": summary["failed_request_count"],
            "average_tokens_per_run": summary["average_tokens_per_completed_run"],
        }

    async def async_added_to_hass(self) -> None:
        """Refresh event-driven period state when the local date rolls over."""
        await super().async_added_to_hass()
        self._reschedule_period_rollover()
        self.async_on_remove(self._remove_period_rollover)
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_CORE_CONFIG_UPDATE, self._handle_core_config_update
            )
        )

    @callback
    def _remove_period_rollover(self) -> None:
        cancel, self._cancel_period_rollover = self._cancel_period_rollover, None
        if cancel is not None:
            cancel()

    @callback
    def _reschedule_period_rollover(self) -> None:
        self._remove_period_rollover()
        zone = dt_util.DEFAULT_TIME_ZONE
        tomorrow = dt_util.now().date() + timedelta(days=1)
        boundary = resolve_wall_time(tomorrow, time.min, zone).astimezone(UTC)
        self._cancel_period_rollover = async_track_point_in_utc_time(
            self.hass, self._handle_period_rollover, boundary
        )

    @callback
    def _handle_core_config_update(self, event: Event) -> None:
        """Rebase the current period and next midnight when HA changes timezone."""
        if "time_zone" in event.data:
            self._reschedule_period_rollover()
            self.async_write_ha_state()

    @callback
    def _handle_period_rollover(self, _now: datetime) -> None:
        """Publish the new local day/month even when there is no new usage."""
        self._cancel_period_rollover = None
        self._reschedule_period_rollover()
        self.async_write_ha_state()


class UsageTodaySensor(_PeriodUsageSensor):
    """Tokens used during the current Home Assistant local day."""

    _attr_translation_key = "usage_today"

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_usage_today"

    def _summary(self) -> dict:
        return self._usage.today_summary()


class UsageMonthSensor(_PeriodUsageSensor):
    """Tokens derived from daily aggregates for the current local month."""

    _attr_translation_key = "usage_month"

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_usage_month"

    def _summary(self) -> dict:
        return self._usage.month_summary()

    def _period_start(self, today: date) -> date:
        return today.replace(day=1)


class LastResponseUsageSensor(UsageSensor):
    """Bounded usage for the latest finalized user turn."""

    _attr_translation_key = "last_response_usage"
    _attr_state_class = None
    # Run detail expires with time even without another accounting event.
    _attr_should_poll = True

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_last_response_usage"

    @property
    def native_value(self) -> int | None:
        return self._usage.latest_run.total_tokens if self._usage.latest_run else None

    @property
    def extra_state_attributes(self) -> dict:
        run = self._usage.latest_run
        if run is None:
            return {}
        return {
            "completed_at": run.completed_at,
            "duration_ms": run.duration_ms,
            "models": run.models,
            "providers": run.providers,
            "api_request_count": run.request_count,
            "tool_call_count": run.tool_call_count,
            "input": run.input_tokens,
            "output": run.output_tokens,
            "cached_input": run.cached_input_tokens,
            "reasoning": run.reasoning_tokens,
            "success": run.successful,
            "error_type": run.error_type,
        }


def rebind_usage_sensors(
    hass: HomeAssistant,
    subentry_id: str,
    old_usage: UsageManager,
    new_usage: UsageManager,
) -> int:
    """Rebind loaded Usage sensors after volatile-to-durable recovery."""
    component = hass.data.get(sensor_component.DATA_COMPONENT)
    rebound = 0
    for entity in getattr(component, "entities", ()):
        if (
            isinstance(entity, UsageSensor)
            and entity._subentry_id == subentry_id
            and entity._usage is old_usage
            and entity.rebind_usage(new_usage)
        ):
            rebound += 1
    return rebound
