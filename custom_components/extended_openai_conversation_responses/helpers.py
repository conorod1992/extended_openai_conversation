"""Helper functions for Extended OpenAI Conversation (Responses) component."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from inspect import isawaitable
import logging
import os
from typing import Annotated, Any, cast, get_args, get_origin
from urllib.parse import SplitResult, urlsplit

from openai import AsyncAzureOpenAI, AsyncClient, AsyncOpenAI

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.template import Template

from .const import DEFAULT_API_PROVIDER, DEFAULT_CONF_BASE_URL
from .entity_context_cache import (
    get_entity_prompt_metadata,
    normalize_entity_aliases as normalize_entity_aliases,
)
from .ha_permissions import filter_entities_for_active_user
from .model_capabilities import select_api_path
from .model_catalog import compatibility_capabilities, model_metadata
from .provider_errors import provider_transport_error

_LOGGER = logging.getLogger(__name__)


AZURE_DOMAINS = ("openai.azure.com", "azure-api.net", "services.ai.azure.com")
_OPENAI_ENDPOINT = urlsplit(DEFAULT_CONF_BASE_URL)
_OPENAI_HOSTED_TOOL_HOSTS = frozenset(
    {_OPENAI_ENDPOINT.hostname, "us.api.openai.com", "eu.api.openai.com"}
)


def get_api_mode(
    configured_mode: str,
    model: str,
    tools_required: bool = False,
    effort: str | None = None,
    web_search: bool = False,
) -> str:
    """Resolve/validate API mode from authoritative model capability data."""
    return select_api_path(model, configured_mode, tools_required, effort, web_search)


def get_model_config(model: str, *, provider: str | None = None) -> dict[str, Any]:
    """Return legacy-compatible booleans derived from v2 capability data.

    `supports_max_tokens` is deliberately always false. Older call sites that still
    branch on this helper therefore normalize onto max_completion_tokens instead of
    ever emitting the deprecated max_tokens field.
    """
    return compatibility_capabilities(
        model, metadata=model_metadata(model, provider=provider)
    )


def get_reasoning_effort_options(
    model: str, *, provider: str | None = None
) -> list[str]:
    """Return the exact model-specific reasoning enum from the active catalogue."""
    return list(model_metadata(model, provider=provider)["reasoning"]["efforts"])


def get_exposed_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Get Assist-exposed entities the authenticated caller may read."""
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
    """Classify supported Azure hostnames without inspecting other URL fields."""
    if not base_url:
        return False
    try:
        hostname = urlsplit(base_url).hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    hostname = hostname.lower()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in AZURE_DOMAINS
    )


def effective_api_provider(api_provider: str | None, base_url: str | None) -> str:
    """Use the same Azure hostname inference for clients and capability consumers."""
    return (
        "azure"
        if api_provider == "azure" or is_azure_url(base_url)
        else (api_provider or DEFAULT_API_PROVIDER)
    )


def entry_api_provider(entry: Any) -> str:
    """Resolve capability context from the parent connection, never its model."""
    data = getattr(entry, "data", {})
    return effective_api_provider(data.get("api_provider"), data.get("base_url"))


def _is_azure_v1_endpoint(base_url: str | None) -> bool:
    """Recognize the GA path after the endpoint has been validated."""
    return bool(base_url and urlsplit(base_url).path.rstrip("/") == "/openai/v1")


def _absolute_http_endpoint(base_url: str) -> SplitResult:
    # urlsplit strips some leading whitespace and embedded controls that the
    # SDK either rejects or treats as a different, relative URL.
    if (
        not isinstance(base_url, str)
        or base_url.startswith(" ")
        or any(ord(char) < 32 or ord(char) == 127 for char in base_url)
    ):
        raise ValueError
    endpoint = urlsplit(base_url)
    _ = endpoint.port
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or any(char.isspace() for char in endpoint.netloc)
    ):
        raise ValueError
    return endpoint


def supports_openai_hosted_tools(
    api_provider: str | None, base_url: str | None
) -> bool:
    """Return whether the entry uses OpenAI's native hosted tools endpoint."""
    if api_provider not in {None, DEFAULT_API_PROVIDER}:
        return False
    if base_url is None or base_url == "":
        return True
    if not isinstance(base_url, str):
        return False
    try:
        endpoint = _absolute_http_endpoint(base_url)
        return (
            endpoint.scheme == _OPENAI_ENDPOINT.scheme
            and endpoint.hostname in _OPENAI_HOSTED_TOOL_HOSTS
            and endpoint.port in {None, 443}
            and endpoint.path.rstrip("/") == _OPENAI_ENDPOINT.path
            and endpoint.username is None
            and endpoint.password is None
            and not endpoint.query
            and not endpoint.fragment
        )
    except ValueError:
        return False


class ProviderEndpointError(HomeAssistantError):
    """A locally invalid provider endpoint."""


def resolve_provider_endpoint(
    api_provider: str | None, base_url: str | None, api_version: str | None
) -> tuple[str, str | None]:
    """Validate and resolve the endpoint before selecting an SDK transport."""
    if base_url == "":
        base_url = None
    if base_url is not None:
        try:
            _absolute_http_endpoint(base_url)
        except ValueError:
            raise ProviderEndpointError(
                "Provider field base_url must be an absolute HTTP or HTTPS URL."
            ) from None

    provider = effective_api_provider(api_provider, base_url)
    if provider == "azure":
        if not base_url or supports_openai_hosted_tools(DEFAULT_API_PROVIDER, base_url):
            raise ProviderEndpointError("Azure OpenAI requires a custom base URL.")
        if not _is_azure_v1_endpoint(base_url):
            # AsyncAzureOpenAI uses this default for existing entries that omit
            # the field. An explicit version takes precedence over it.
            if api_version is None:
                api_version = os.environ.get("OPENAI_API_VERSION")
            if not isinstance(api_version, str) or not api_version.strip():
                raise ProviderEndpointError(
                    "Azure OpenAI requires provider field api_version."
                )
    elif (
        base_url is None
        and (sdk_base_url := os.environ.get("OPENAI_BASE_URL")) is not None
    ):
        try:
            _absolute_http_endpoint(sdk_base_url)
        except ValueError:
            raise ProviderEndpointError(
                "OPENAI_BASE_URL must be an absolute HTTP or HTTPS URL."
            ) from None
    return provider, base_url


def is_native_openai_connection(data: Mapping[str, Any], *, client: Any = None) -> bool:
    """Classify the configured endpoint or the SDK's effective default."""
    base_url = data.get("base_url")
    if base_url is None or base_url == "":
        sdk_url = getattr(client, "base_url", None)
        # SDK versions expose their own HTTPX URL types. Both have a string host.
        if isinstance(sdk_url, str) or isinstance(getattr(sdk_url, "host", None), str):
            base_url = str(sdk_url)
        else:
            base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url == "":
            return False
    # An inferred Azure connection necessarily has a different endpoint.
    return supports_openai_hosted_tools(data.get("api_provider"), base_url)


def get_token_param_for_model(model: str) -> str:
    """Return the modern Chat Completions output-token field.

    This compatibility helper intentionally never returns deprecated `max_tokens`.
    Responses requests use max_output_tokens through the request builder instead.
    """
    del model
    return "max_completion_tokens"


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


def _warm_response_stream_adapter() -> None:
    """Best-effort compatibility shim for the SDK's cached union validator."""
    try:
        from openai._models import TypeAdapter
        from openai.types.responses import ResponseStreamEvent

        # SDK 2.21/2.45/3.10/3.26 construct_type strips Annotated before
        # validate_type uses this cached factory. A public Pydantic TypeAdapter
        # would not populate that cache. Skip unfamiliar/uncached SDK versions.
        if get_origin(ResponseStreamEvent) is not Annotated or not callable(
            getattr(TypeAdapter, "cache_info", None)
        ):
            return
        TypeAdapter(get_args(ResponseStreamEvent)[0])
    except Exception:
        # Private imports, type shapes and adapter construction may change.
        # Only this optional optimization may fail without affecting setup.
        pass


def _prepare_openai_client(client: AsyncClient) -> None:
    """Resolve synchronous SDK metadata/resources before loop-side wrappers."""
    client.platform_headers()
    # These cached resources are also accessed by the debug/performance proxies.
    # Access only: no requests, transports or additional clients are created.
    _ = client.responses, client.chat.completions, client.embeddings, client.models
    _warm_response_stream_adapter()


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

    provider, base_url = resolve_provider_endpoint(api_provider, base_url, api_version)
    # Match HA core: retain its shared HTTPX client and TLS/connection policy.
    # SDK 3 supports this client at runtime, while typing targets HTTPX2.
    client: AsyncClient
    if provider == "azure" and not _is_azure_v1_endpoint(base_url):
        assert base_url is not None  # Required by resolve_provider_endpoint.
        client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=base_url,
            api_version=api_version,
            organization=organization,
            http_client=cast(Any, get_async_client(hass)),
        )
    else:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            http_client=cast(Any, get_async_client(hass)),
        )

    # Prime SDK metadata and lazy resource imports in the existing executor job,
    # including when authentication is skipped, before wrappers access them.
    await hass.async_add_executor_job(partial(_prepare_openai_client, client))

    if skip_authentication:
        return client

    try:
        response = await hass.async_add_executor_job(
            partial(client.models.list, timeout=10)
        )

        # SDK 2 returns an async paginator immediately; SDK 3 returns a
        # coroutine. Materialize either awaitable exactly once before iterating.
        page = await response if isawaitable(response) else response
        async for _ in page:
            break
    except (TimeoutError, ConnectionError) as err:
        raise provider_transport_error(err) from err
    return client
