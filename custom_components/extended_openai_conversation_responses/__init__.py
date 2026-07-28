"""The Extended OpenAI Conversation (Responses) integration."""

from __future__ import annotations

import logging

from openai import AsyncClient
from openai._exceptions import AuthenticationError, OpenAIError

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_PROVIDER,
    DEFAULT_SKIP_AUTHENTICATION,
    DOMAIN,
    LEGACY_CONTEXT_TRUNCATE_STRATEGY,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_OFF,
)
from .helpers import get_authenticated_client
from .memory import get_memory_mode
from .memory_ui import async_setup_memory_ui
from .services import async_setup_services
from .template import async_setup_templates, async_unload_templates

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.AI_TASK, Platform.CONVERSATION, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ExtendedOpenAIConfigEntry = ConfigEntry[AsyncClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Extended OpenAI Conversation (Responses)."""
    await async_migrate_integration(hass)
    await async_setup_services(hass, config)
    await async_setup_memory_ui(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ExtendedOpenAIConfigEntry
) -> bool:
    """Set up Extended OpenAI Conversation (Responses) from a config entry."""

    try:
        client = await get_authenticated_client(
            hass=hass,
            api_key=entry.data[CONF_API_KEY],
            base_url=entry.data.get(CONF_BASE_URL),
            api_version=entry.data.get(CONF_API_VERSION),
            organization=entry.data.get(CONF_ORGANIZATION),
            skip_authentication=entry.data.get(
                CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION
            ),
            api_provider=entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        )
    except AuthenticationError as err:
        _LOGGER.error("Invalid API key: %s", err)
        return False
    except OpenAIError as err:
        raise ConfigEntryNotReady(err) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await async_setup_templates(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenAI."""
    await async_unload_templates(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_integration(hass: HomeAssistant) -> None:
    """Migrate integration entry structure."""

    # Make sure we get enabled config entries first
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda e: e.disabled_by is not None,
    )
    if not any(entry.version < 3 for entry in entries):
        return

    for entry in entries:
        if entry.version >= 3:
            continue
        _LOGGER.warning(
            "Migrating Extended OpenAI Conversation (Responses) config entry %s from version %s to version 3",
            entry.entry_id,
            entry.version,
        )
        if entry.version == 1:
            subentry = ConfigSubentry(
                data=entry.options,
                subentry_type="conversation",
                title=entry.title,
                unique_id=None,
            )
            hass.config_entries.async_add_subentry(entry, subentry)
            hass.config_entries.async_update_entry(
                entry, title=entry.title, options={}, version=2
            )

        for subentry in entry.subentries.values():
            if subentry.subentry_type != "conversation":
                continue
            data = dict(subentry.data)
            mode = get_memory_mode(data)
            data[CONF_MEMORY_MODE] = mode
            data[CONF_MEMORY_ENABLED] = mode != MEMORY_MODE_OFF
            data[CONF_MEMORY_AUTO_CREATE] = mode == MEMORY_MODE_AUTOMATIC
            data.setdefault(
                CONF_CONTEXT_TRUNCATE_STRATEGY,
                LEGACY_CONTEXT_TRUNCATE_STRATEGY,
            )
            hass.config_entries.async_update_subentry(entry, subentry, data=data)
        hass.config_entries.async_update_entry(entry, version=3)
