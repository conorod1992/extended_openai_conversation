"""Diagnostic usage sensors for conversation agents."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ExtendedOpenAIConfigEntry
from .const import CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL, DOMAIN
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
        async_add_entities(
            [UsageSensor(subentry, usage)],
            config_subentry_id=subentry.subentry_id,
        )


class UsageSensor(SensorEntity):
    """Expose cumulative token usage with request counts as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "usage"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = "tokens"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

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
    def native_value(self) -> int:
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
