"""Helper functions for Extended OpenAI Conversation (Responses) component."""

from __future__ import annotations

from functools import partial
import logging
import re
from typing import Any, cast

from openai import AsyncAzureOpenAI, AsyncClient, AsyncOpenAI

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.template import Template

from .const import (
    API_MODE_AUTO,
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    DEFAULT_API_PROVIDER,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_MODEL_CONFIG,
    DEFAULT_TOKEN_PARAM,
    MODEL_CONFIG_PATTERNS,
    MODEL_TOKEN_PARAMETER_SUPPORT,
)
from .entity_context_cache import get_entity_prompt_metadata, normalize_entity_aliases
from .ha_permissions import filter_entities_for_active_user

_LOGGER = logging.getLogger(__name__)


AZURE_DOMAIN_PATTERN = r"\.(openai\.azure\.com|azure-api\.net|services\.ai\.azure\.com)"


def get_api_mode(configured_mode: str, model: str) -> str:
    """Resolve the configured API mode for a model.

    Auto deliberately has a conservative boundary so existing models and
    OpenAI-compatible providers keep using Chat Completions. GPT-5.6 and later
    GPT-5 minor versions use Responses, where reasoning and function tools can
    be used together.
    """
    if configured_mode != API_MODE_AUTO:
        return configured_mode

    match = re.match(r"^gpt-5\.(\d+)(?:[-.]|$)", model, re.IGNORECASE)
    if match and int(match.group(1)) >= 6:
        return API_MODE_RESPONSES

    return API_MODE_CHAT_COMPLETIONS


def get_model_config(model: str) -> dict[str, bool]:
    """Get model-specific parameter configuration."""
    # Check patterns in order; first match wins
    for entry in MODEL_CONFIG_PATTERNS:
        pattern = str(entry["pattern"])
        entry_config = entry["config"]
        if re.match(pattern, model, re.IGNORECASE):
            # Type assertion since we know the structure from MODEL_CONFIG_PATTERNS
            return (
                dict(entry_config)
                if isinstance(entry_config, dict)
                else DEFAULT_MODEL_CONFIG
            )

    # Default configuration for standard models (gpt-4, gpt-4o, etc.)
    return DEFAULT_MODEL_CONFIG


def get_exposed_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Get Assist-exposed entities the authenticated caller may read."""
    # Exposure, state values/names and caller permissions deliberately stay live.
    # Only registry-derived aliases/area metadata are cached behind registry-event
    # invalidation in entity_context_cache.
    states = [
        state
        for state in hass.states.async_all()
        if async_should_expose(hass, conversation.DOMAIN, state.entity_id)
    ]
    exposed_entities = []
    for state in states:
        metadata = get_entity_prompt_metadata(hass, state.entity_id)
        exposed_entities.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "state": state.state,
                "aliases": list(metadata.aliases),
            }
        )
    return filter_entities_for_active_user(hass, exposed_entities)


def is_azure_url(base_url: str | None) -> bool:
    """Check if the base URL is an Azure OpenAI URL."""
    return bool(base_url and re.search(AZURE_DOMAIN_PATTERN, base_url))


def supports_openai_hosted_tools(
    api_provider: str | None, base_url: str | None
) -> bool:
    """Return whether the entry uses OpenAI's native hosted tools endpoint."""
    if api_provider not in {None, DEFAULT_API_PROVIDER}:
        return False
    return not base_url or base_url.rstrip("/") == DEFAULT_CONF_BASE_URL.rstrip("/")


def get_token_param_for_model(model: str) -> str:
    """Return the token parameter name for a model."""
    model_lower = model.lower()
    for entry in MODEL_TOKEN_PARAMETER_SUPPORT:
        if re.search(entry["pattern"], model_lower):
            return entry["token_param"]
    return DEFAULT_TOKEN_PARAM


def convert_to_template(
    settings: Any,
    template_keys: list[str] | None = None,
    hass: HomeAssistant | None = None,
) -> None:
    if template_keys is None:
        template_keys = ["data", "event_data", "target", "service"]
    _convert_to_template(settings, template_keys, hass, [])


def _convert_to_template(
    settings: Any,
    template_keys: list[str],
    hass: HomeAssistant | None,
    parents: list[str],
) -> None:
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(value, str) and (
                key in template_keys or set(parents).intersection(template_keys)
            ):
                settings[key] = Template(value, cast(HomeAssistant, hass))
            if isinstance(value, dict):
                parents.append(key)
                _convert_to_template(value, template_keys, hass, parents)
                parents.pop()
            if isinstance(value, list):
                parents.append(key)
                for item in value:
                    _convert_to_template(item, template_keys, hass, parents)
                parents.pop()
    if isinstance(settings, list):
        for setting in settings:
            _convert_to_template(setting, template_keys, hass, parents)


async def get_authenticated_client(
    hass: HomeAssistant,
    api_key: str,
    base_url: str | None,
    api_version: str | None,
    organization: str | None,
    api_provider: str | None,
    skip_authentication: bool = False,
) -> AsyncClient:
    """Validate OpenAI authentication."""

    client: AsyncClient
    if base_url and (is_azure_url(base_url) or api_provider == "azure"):
        client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=base_url,
            api_version=api_version,
            organization=organization,
            http_client=get_async_client(hass),
        )
    else:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            http_client=get_async_client(hass),
        )

    if skip_authentication:
        return client

    response = await hass.async_add_executor_job(
        partial(client.models.list, timeout=10)
    )

    async for _ in response:
        break
    return client
