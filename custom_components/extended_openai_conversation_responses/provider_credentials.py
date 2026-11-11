"""Safe management of parent-entry provider credentials."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAIError
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    API_PROVIDERS,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_PROVIDER,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_SKIP_AUTHENTICATION,
    DOMAIN,
)
from .ha_schema import websocket_command as ha_websocket_command
from .helpers import effective_api_provider, get_authenticated_client
from .operational_errors import log_handled_failure
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


def validate_provider_connection(connection: Any) -> str:
    """Validate persisted client settings locally and return the effective provider."""
    if not isinstance(connection, Mapping):
        raise HomeAssistantError("The provider connection must be an object.")
    api_key = connection.get(CONF_API_KEY)
    if not isinstance(api_key, str) or not api_key.strip():
        raise HomeAssistantError("Provider field api_key must be a non-empty string.")
    if not isinstance(connection.get(CONF_SKIP_AUTHENTICATION, False), bool):
        raise HomeAssistantError(
            "Provider field skip_authentication must be a boolean."
        )
    for field in (CONF_BASE_URL, CONF_API_VERSION, CONF_ORGANIZATION):
        if (value := connection.get(field)) is not None and not isinstance(value, str):
            raise HomeAssistantError(f"Provider field {field} must be a string.")

    provider = connection.get(CONF_API_PROVIDER)
    if provider is not None and provider not in (
        option["key"] for option in API_PROVIDERS
    ):
        raise HomeAssistantError(
            "Unsupported API provider (api_provider). Choose 'openai' or 'azure'."
        )
    base_url = connection.get(CONF_BASE_URL)
    if base_url is not None:
        try:
            endpoint = urlsplit(base_url)
            _ = endpoint.port
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or any(char.isspace() for char in endpoint.netloc)
            ):
                raise ValueError
        except ValueError:
            raise HomeAssistantError(
                "Provider field base_url must be an absolute HTTP or HTTPS URL."
            ) from None
    provider = effective_api_provider(provider, base_url)
    if provider == "azure":
        if not base_url or base_url.rstrip("/") == DEFAULT_CONF_BASE_URL.rstrip("/"):
            raise HomeAssistantError("Azure OpenAI requires a custom base URL.")
        if not (connection.get(CONF_API_VERSION) or "").strip():
            raise HomeAssistantError(
                "Azure OpenAI requires provider field api_version."
            )
    return provider


def validate_agent_provider_request(
    subentry_type: str, options: Mapping[str, Any], provider_data: Mapping[str, Any]
) -> None:
    """Use the agent's production request validation before changing its provider."""
    from .request import build_provider_request_snapshot
    from .request_rules import validate_routed_request_options

    if subentry_type == "ai_task_data":
        build_provider_request_snapshot(options, provider_data, tools_required=False)
    else:
        validate_routed_request_options(options, provider_data)


def incompatible_provider_agent(
    title: str, err: HomeAssistantError
) -> HomeAssistantError:
    """Identify one incompatible agent in a bounded message."""
    return HomeAssistantError(
        f"Provider change is incompatible with agent {title[:100]}: {str(err)[:400]}"
    )


def restore_private_provider_connection(
    hass: HomeAssistant,
    entry: Any,
    connection: dict[str, Any],
    expected_data: dict[str, Any],
    *,
    confirmed: bool,
) -> None:
    """Restore captured credentials only after explicit shared-scope consent.

    This is a separate connection update after agent restoration. A concurrent
    connection edit must never be overwritten, even when agent restore succeeded.
    """
    if not confirmed:
        raise HomeAssistantError(
            "Confirm restoring the provider connection shared by other agents"
        )
    if not isinstance(connection, dict) or not connection:
        raise HomeAssistantError("This private backup has no provider connection")
    if dict(entry.data) != expected_data:
        raise HomeAssistantError(
            "The provider connection changed after preview; import and preview again"
        )
    hass.config_entries.async_update_entry(entry, data=deepcopy(connection))
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _validation_error(error: OpenAIError) -> HomeAssistantError:
    """Return a bounded credential error that never includes provider response text."""
    category = classify_config_provider_error(error)
    message = _VALIDATION_MESSAGES.get(
        category, "The new API key could not be validated."
    )
    return HomeAssistantError(f"{message} The existing API key was not changed.")


def request_provider_reload_after_update(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    state_before: ConfigEntryState,
    *,
    changed: bool,
    validation_performed: bool,
) -> bool:
    """Share post-save recovery without duplicating the loaded update listener."""
    if entry.disabled_by is not None:
        return False
    if state_before is ConfigEntryState.LOADED:
        if changed:
            return True  # The normal update listener owns this reload.
        if not validation_performed:
            return False
    elif not state_before.recoverable:
        return False
    # Failed startup has no update listener. Successful validation of an unchanged
    # key can also repair provider-side access and warrants a fresh setup attempt.
    hass.config_entries.async_schedule_reload(entry.entry_id)
    return True


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
        log_handled_failure(
            logging.getLogger(__name__),
            f"API-key validation rejected entry={entry.entry_id} category={classify_config_provider_error(err)}; existing credential retained",
            err,
            level=logging.DEBUG,
        )
        raise _validation_error(err) from err
    except Exception as err:
        log_handled_failure(
            logging.getLogger(__name__),
            f"API-key validation failed entry={entry.entry_id}; existing credential retained",
            err,
        )
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
    reload_requested = request_provider_reload_after_update(
        hass,
        entry,
        state_before,
        changed=changed,
        validation_performed=not skip_authentication,
    )

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


@ha_websocket_command(
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
