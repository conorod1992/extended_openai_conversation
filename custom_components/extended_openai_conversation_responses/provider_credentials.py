"""Safe management of parent-entry provider credentials."""

from __future__ import annotations

from typing import Any

from openai import OpenAIError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui
from .const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_PROVIDER,
    DEFAULT_SKIP_AUTHENTICATION,
)
from .helpers import get_authenticated_client
from .provider_errors import classify_config_provider_error

_FRONTEND_MODULE = "management-provider-credentials.js"
_VALIDATION_MESSAGES = {
    "invalid_auth": "The provider rejected the new API key.",
    "cannot_connect": "Could not connect to the configured provider while validating the new API key.",
    "provider_forbidden": "The provider refused access for the new API key or account permissions.",
    "provider_rate_limited": "The provider rate limit prevented validation of the new API key. Try again later.",
    "provider_unavailable": "The provider is temporarily unavailable, so the new API key could not be validated.",
    "provider_error": "The provider rejected the credential validation request.",
}


def _register_frontend_module() -> None:
    """Expose the credential UI module before management static paths are created."""
    modules = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, _FRONTEND_MODULE))
    )
    setattr(management_ui, "MANAGEMENT_FRONTEND_MODULES", modules)  # noqa: B010


_register_frontend_module()


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

    candidate = dict(entry.data)
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

    hass.config_entries.async_update_entry(entry, data=candidate)
    return {
        "updated": True,
        "validation_performed": not skip_authentication,
        "provider": str(candidate.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)),
    }
