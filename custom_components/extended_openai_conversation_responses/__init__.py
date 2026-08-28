"""The Extended OpenAI Conversation (Responses) integration."""

from __future__ import annotations

import logging

from openai import AsyncClient
from openai._exceptions import AuthenticationError, OpenAIError

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_BASE_URL,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_CURRENT_DATETIME_TEMPLATE,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_EXPOSED_ENTITIES_TEMPLATE,
    CONF_FUNCTION_GROUPS,
    CONF_GUEST_CONTROLLABLE_AREAS,
    CONF_GUEST_CONTROLLABLE_DOMAINS,
    CONF_GUEST_CONTROLLABLE_ENTITIES,
    CONF_GUEST_CONTROLLABLE_LABELS,
    CONF_GUEST_KNOWLEDGE_ENABLED,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_READABLE_AREAS,
    CONF_GUEST_READABLE_DOMAINS,
    CONF_GUEST_READABLE_ENTITIES,
    CONF_GUEST_READABLE_LABELS,
    CONF_GUEST_SHARED_MEMORY_READ,
    CONF_GUEST_SHARED_MEMORY_WRITE,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONF_ORGANIZATION,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    CONFIG_ENTRY_VERSION,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    DEFAULT_CURRENT_DATETIME_TEMPLATE,
    DEFAULT_EXPOSED_ENTITIES_TEMPLATE,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_SHARED_ARCHIVE_ENABLED,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_SKIP_AUTHENTICATION,
    DEFAULT_SPEECH_PROCESSING_ENABLED,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    DOMAIN,
    LEGACY_CONTEXT_TRUNCATE_STRATEGY,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_OFF,
)
from .helpers import get_authenticated_client
from .intercom_services import async_setup_intercom_services
from .management_ui import async_setup_management_ui
from .memory import get_memory_mode
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
    await async_setup_intercom_services(hass)
    await async_setup_management_ui(hass)
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
        raise ConfigEntryAuthFailed("API credentials are invalid or expired") from err
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

    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda e: e.disabled_by is not None,
    )
    if not any(entry.version < CONFIG_ENTRY_VERSION for entry in entries):
        return

    for entry in entries:
        if entry.version >= CONFIG_ENTRY_VERSION:
            continue
        _LOGGER.warning(
            "Migrating Extended OpenAI Conversation (Responses) config entry %s from version %s to version %s",
            entry.entry_id,
            entry.version,
            CONFIG_ENTRY_VERSION,
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
            data.setdefault(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
            data.setdefault(CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS)
            data.setdefault(
                CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
                DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
            )
            data.setdefault(CONF_SHARED_ARCHIVE_ENABLED, DEFAULT_SHARED_ARCHIVE_ENABLED)
            data.setdefault(
                CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
                DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
            )
            data.setdefault(CONF_VOICE_SCOPE_POLICY, DEFAULT_VOICE_SCOPE_POLICY)
            data.setdefault(CONF_VOICE_UNMAPPED_POLICY, DEFAULT_VOICE_UNMAPPED_POLICY)
            data.setdefault(CONF_VOICE_DEVICE_MAPPINGS, {})
            data.setdefault(CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE)
            data.setdefault(
                CONF_USAGE_REQUEST_RETENTION_DAYS,
                DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
            )
            data.setdefault(
                CONF_USAGE_RUN_RETENTION_DAYS,
                DEFAULT_USAGE_RUN_RETENTION_DAYS,
            )
            data.setdefault(
                CONF_SPEECH_PROCESSING_ENABLED,
                DEFAULT_SPEECH_PROCESSING_ENABLED,
            )
            data.setdefault(CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN)
            data.setdefault(CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS)
            data.setdefault(
                CONF_SPEECH_REGEX_REPLACEMENTS,
                list(DEFAULT_SPEECH_REGEX_REPLACEMENTS),
            )
            data.setdefault(CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS))
            data.setdefault(CONF_GUEST_MODE_ENABLED, False)
            data.setdefault(CONF_GUEST_SHARED_MEMORY_READ, False)
            data.setdefault(CONF_GUEST_SHARED_MEMORY_WRITE, False)
            data.setdefault(CONF_GUEST_KNOWLEDGE_ENABLED, False)
            for guest_selector in (
                CONF_GUEST_READABLE_ENTITIES,
                CONF_GUEST_CONTROLLABLE_ENTITIES,
                CONF_GUEST_READABLE_DOMAINS,
                CONF_GUEST_CONTROLLABLE_DOMAINS,
                CONF_GUEST_READABLE_AREAS,
                CONF_GUEST_CONTROLLABLE_AREAS,
                CONF_GUEST_READABLE_LABELS,
                CONF_GUEST_CONTROLLABLE_LABELS,
            ):
                data.setdefault(guest_selector, [])
            data.setdefault(CONF_CURRENT_DATETIME_ENABLED, False)
            data.setdefault(
                CONF_CURRENT_DATETIME_TEMPLATE, DEFAULT_CURRENT_DATETIME_TEMPLATE
            )
            data.setdefault(CONF_EXPOSED_ENTITIES_ENABLED, False)
            data.setdefault(
                CONF_EXPOSED_ENTITIES_TEMPLATE, DEFAULT_EXPOSED_ENTITIES_TEMPLATE
            )
            hass.config_entries.async_update_subentry(entry, subentry, data=data)
        hass.config_entries.async_update_entry(entry, version=CONFIG_ENTRY_VERSION)
