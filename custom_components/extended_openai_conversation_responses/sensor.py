"""Diagnostic usage sensors for conversation agents."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from . import ExtendedOpenAIConfigEntry
from .const import (
    CONF_CHAT_MODEL,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DOMAIN,
)
from .guest_mode import GuestModeManager, async_get_guest_mode
from .usage import UsageManager, async_get_usage


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ExtendedOpenAIConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one compact diagnostic sensor per conversation agent."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        usage = await async_get_usage(hass, config_entry.entry_id, subentry.subentry_id)
        guest_mode = await async_get_guest_mode(
            hass, config_entry.entry_id, subentry.subentry_id
        )
        usage.request_retention_days = int(
            subentry.data.get(
                CONF_USAGE_REQUEST_RETENTION_DAYS,
                DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
            )
        )
        usage.run_retention_days = int(
            subentry.data.get(
                CONF_USAGE_RUN_RETENTION_DAYS, DEFAULT_USAGE_RUN_RETENTION_DAYS
            )
        )
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
    def native_value(self) -> str:
        return str(self._guest_mode.status()["state"])

    @property
    def extra_state_attributes(self) -> dict:
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
    _attr_state_class: SensorStateClass | None = SensorStateClass.TOTAL_INCREASING

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        """Initialize the diagnostic sensor."""
        self._usage = usage
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
    def extra_state_attributes(self) -> dict:
        """Expose the distinct conversation, request, and token counters."""
        return self._usage.as_dict()

    async def async_added_to_hass(self) -> None:
        """Subscribe to persisted usage updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._usage.async_add_listener(self.async_write_ha_state))


class _PeriodUsageSensor(UsageSensor):
    """Common event-driven period sensor behavior."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)

    def _summary(self) -> dict:
        raise NotImplementedError

    @property
    def native_value(self) -> int:
        return int(self._summary()["total_tokens"])

    @property
    def extra_state_attributes(self) -> dict:
        summary = self._summary()
        return {
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
        self.async_on_remove(
            async_track_time_change(
                self.hass,
                self._handle_period_rollover,
                hour=0,
                minute=0,
                second=0,
            )
        )

    @callback
    def _handle_period_rollover(self, _now: datetime) -> None:
        """Publish the new local day/month even when there is no new usage."""
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


class LastResponseUsageSensor(UsageSensor):
    """Bounded usage for the latest finalized user turn."""

    _attr_translation_key = "last_response_usage"
    _attr_state_class = None

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
