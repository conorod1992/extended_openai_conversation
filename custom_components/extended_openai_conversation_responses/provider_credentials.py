"""Safe management of parent-entry provider credentials."""

from __future__ import annotations

from typing import Any

from openai import OpenAIError
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_PROVIDER,
    DEFAULT_SKIP_AUTHENTICATION,
    DOMAIN,
)
from .helpers import get_authenticated_client
from .provider_errors import classify_config_provider_error

WS_UPDATE_API_KEY = f"{DOMAIN}/management/update_api_key"
_WS_SETUP = f"{DOMAIN}.provider_credentials_ws_setup"
_VALIDATION_MESSAGES = {
    "invalid_auth": "The provider rejected the new API key.",
    "cannot_connect": "Could not connect to the configured provider while validating the new API key.",
    "provider_forbidden": "The provider refused access for the new API key or account permissions.",
    "provider_rate_limited": "The provider rate limit prevented validation of the new API key. Try again later.",
    "provider_unavailable": "The provider is temporarily unavailable, so the new API key could not be validated.",
    "provider_error": "The provider rejected the credential validation request.",
}


def _validation_error(error: OpenAIError) -> HomeAssistantError:
    """Return a bounded credential error that never includes provider response text."""
    category = classify_config_provider_error(error)
    message = _VALIDATION_MESSAGES.get(
        category, "The new API key could not be validated."
    )
    return HomeAssistantError(f"{message} The existing API key was not changed.")


async def async_replace_api_key(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    api_key: Any,
) -> dict[str, Any]:
    """Validate and replace only the parent config entry API key.

    Conversation and AI Task subentries share the parent entry's provider credentials.
    The candidate key is never written until validation succeeds, and it is never
    returned to the management client.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise HomeAssistantError("Enter a new API key.")

    original_data = dict(entry.data)
    candidate = dict(original_data)
    candidate[CONF_API_KEY] = api_key
    skip_authentication = bool(
        candidate.get(CONF_SKIP_AUTHENTICATION, DEFAULT_SKIP_AUTHENTICATION)
    )

    try:
        await get_authenticated_client(
            hass=hass,
            api_key=api_key,
            base_url=candidate.get(CONF_BASE_URL),
            api_version=candidate.get(CONF_API_VERSION),
            organization=candidate.get(CONF_ORGANIZATION),
            skip_authentication=skip_authentication,
            api_provider=candidate.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        )
    except OpenAIError as err:
        raise _validation_error(err) from err
    except Exception as err:
        raise HomeAssistantError(
            "The new API key could not be validated. The existing API key was not changed."
        ) from err

    if dict(entry.data) != original_data:
        raise HomeAssistantError(
            "Provider settings changed while the new API key was being validated. "
            "The API key was not changed by this operation. Try again."
        )

    state_before = entry.state
    changed = hass.config_entries.async_update_entry(entry, data=candidate)
    reload_requested = False
    if state_before is ConfigEntryState.LOADED:
        if changed:
            # Successful setup registers this integration's normal update listener,
            # which reloads the entry after a changed config-entry update.
            reload_requested = True
        elif not skip_authentication:
            # A successful validation can repair provider-side access without
            # changing the credential string. Reload once so any pending native
            # reauth flow/runtime failure state is cleared consistently.
            hass.config_entries.async_schedule_reload(entry.entry_id)
            reload_requested = True
    elif entry.disabled_by is None and state_before.recoverable:
        # Startup authentication failures occur before the normal update listener
        # is registered. Retry after successful validation even when the key string
        # is unchanged, since provider-side access may have been repaired meanwhile.
        hass.config_entries.async_schedule_reload(entry.entry_id)
        reload_requested = True

    return {
        "updated": changed,
        "validation_performed": not skip_authentication,
        "reload_requested": reload_requested,
        "provider": str(candidate.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)),
    }


async def _async_update_api_key_command(
    hass: HomeAssistant, message: dict[str, Any]
) -> dict[str, Any]:
    """Resolve and update the exact parent integration entry."""
    entry = hass.config_entries.async_get_entry(message["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    return await async_replace_api_key(hass, entry, message["api_key"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_UPDATE_API_KEY,
        vol.Required("entry_id"): str,
        vol.Required("api_key"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_update_api_key(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate and rotate a parent provider credential for an administrator."""
    try:
        result = await _async_update_api_key_command(hass, msg)
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


def setup_provider_credentials_websocket(hass: HomeAssistant) -> bool:
    """Register the narrow admin-only credential command exactly once."""
    if hass.data.get(_WS_SETUP):
        return False
    websocket_api.async_register_command(hass, websocket_update_api_key)
    hass.data[_WS_SETUP] = True
    return True
