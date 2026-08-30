"""Web functions for HTTP API calls and HTML scraping."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import aiohttp
from bs4 import BeautifulSoup
import voluptuous as vol

from homeassistant.components import rest, scrape
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_METHOD,
    CONF_NAME,
    CONF_PAYLOAD,
    CONF_RESOURCE,
    CONF_RESOURCE_TEMPLATE,
    CONF_TIMEOUT,
    CONF_VALUE_TEMPLATE,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.template import Template

from ..const import CONF_PAYLOAD_TEMPLATE
from ..resource_limits import MAX_REMOTE_RESPONSE_BYTES
from .base import Function

_LOGGER = logging.getLogger(__name__)


class _BoundedResponse:
    """Proxy one aiohttp response while bounding body materialization."""

    def __init__(self, response: aiohttp.ClientResponse, max_bytes: int) -> None:
        self._response = response
        self._max_bytes = max_bytes
        self._body: bytes | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    async def read(self) -> bytes:
        """Read at most the configured response-body limit."""
        if self._body is not None:
            return self._body

        content_length = self._response.content_length
        if content_length is not None and content_length > self._max_bytes:
            raise HomeAssistantError(
                "Remote response exceeds the configured safety limit of "
                f"{self._max_bytes} bytes"
            )

        try:
            body = await self._response.content.readexactly(self._max_bytes + 1)
        except asyncio.IncompleteReadError as err:
            body = err.partial

        if len(body) > self._max_bytes:
            raise HomeAssistantError(
                "Remote response exceeds the configured safety limit of "
                f"{self._max_bytes} bytes"
            )

        self._body = body
        return body

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        """Decode the same bounded body that aiohttp would expose as text."""
        body = await self.read()
        selected_encoding = encoding or self._response.charset or "utf-8"
        return body.decode(selected_encoding, errors=errors)


class _BoundedRequestContext:
    """Wrap aiohttp's request context and expose a bounded response proxy."""

    def __init__(self, request_context: Any, max_bytes: int) -> None:
        self._request_context = request_context
        self._max_bytes = max_bytes

    async def __aenter__(self) -> _BoundedResponse:
        response = await self._request_context.__aenter__()
        return _BoundedResponse(response, self._max_bytes)

    async def __aexit__(self, *args: Any) -> Any:
        return await self._request_context.__aexit__(*args)


class _BoundedClientSession:
    """Delegate to HA's shared aiohttp session with bounded response reads."""

    def __init__(self, session: aiohttp.ClientSession, max_bytes: int) -> None:
        self._session = session
        self._max_bytes = max_bytes

    def request(self, *args: Any, **kwargs: Any) -> _BoundedRequestContext:
        return _BoundedRequestContext(
            self._session.request(*args, **kwargs), self._max_bytes
        )


def _install_bounded_session(
    hass: HomeAssistant, rest_data: rest.data.RestData
) -> None:
    """Make HA RestData use a bounded proxy around its normal shared session."""
    session = async_get_clientsession(
        hass,
        verify_ssl=rest_data._verify_ssl,
        ssl_cipher=rest_data._ssl_cipher_list,
    )
    rest_data._session = cast(
        Any, _BoundedClientSession(session, MAX_REMOTE_RESPONSE_BYTES)
    )


def get_rest_data(
    hass: HomeAssistant, rest_config: dict[str, Any], arguments: dict[str, Any]
) -> rest.data.RestData:
    """Create RestData from config with template rendering and bounded reads."""
    # Runtime function configs contain Home Assistant Template objects, which are not
    # deepcopy-safe. A shallow copy is sufficient because this helper only replaces
    # top-level REST keys and must never mutate the reusable function configuration.
    rendered_config = dict(rest_config)
    rendered_config.setdefault(CONF_METHOD, rest.const.DEFAULT_METHOD)
    rendered_config.setdefault(CONF_VERIFY_SSL, rest.const.DEFAULT_VERIFY_SSL)
    rendered_config.setdefault(CONF_TIMEOUT, rest.data.DEFAULT_TIMEOUT)
    rendered_config.setdefault(rest.const.CONF_ENCODING, rest.const.DEFAULT_ENCODING)

    resource_template: Template | None = rendered_config.get(CONF_RESOURCE_TEMPLATE)
    if resource_template is not None:
        rendered_config.pop(CONF_RESOURCE_TEMPLATE)
        rendered_config[CONF_RESOURCE] = resource_template.async_render(
            arguments, parse_result=False
        )

    payload_template: Template | None = rendered_config.get(CONF_PAYLOAD_TEMPLATE)
    if payload_template is not None:
        rendered_config.pop(CONF_PAYLOAD_TEMPLATE)
        rendered_config[CONF_PAYLOAD] = payload_template.async_render(
            arguments, parse_result=False
        )

    rest_data = rest.create_rest_data_from_config(hass, rendered_config)
    if isinstance(rest_data, rest.data.RestData):
        _install_bounded_session(hass, rest_data)
    return rest_data


class RestFunction(Function):
    """REST tool for HTTP API calls."""

    def __init__(self) -> None:
        """Initialize Rest tool."""
        super().__init__(
            vol.Schema(rest.RESOURCE_SCHEMA).extend(
                {
                    vol.Optional("value_template"): cv.template,
                    vol.Optional("payload_template"): cv.template,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        """Execute REST API call."""
        rest_data = get_rest_data(hass, function_config, arguments)

        await rest_data.async_update()
        value = rest_data.data_without_xml()
        value_template = function_config.get(CONF_VALUE_TEMPLATE)

        if value is not None and value_template is not None:
            value = value_template.async_render_with_possible_json_value(
                value, None, arguments
            )

        return value


class ScrapeFunction(Function):
    """Scrape tool for HTML content extraction."""

    def __init__(self) -> None:
        """Initialize Scrape tool."""
        super().__init__(
            scrape.COMBINED_SCHEMA.extend(
                {
                    vol.Optional("value_template"): cv.template,
                    vol.Optional("payload_template"): cv.template,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        """Execute web scraping."""
        rest_data = get_rest_data(hass, function_config, arguments)
        coordinator = scrape.coordinator.ScrapeCoordinator(
            hass,
            None,
            rest_data,
            function_config,
            scrape.const.DEFAULT_SCAN_INTERVAL,
        )
        await coordinator.async_refresh()

        new_arguments = dict(arguments)

        for sensor_config in function_config["sensor"]:
            name: Template = sensor_config.get(CONF_NAME)
            value = self._async_update_from_rest_data(
                coordinator.data, sensor_config, arguments
            )
            new_arguments["value"] = value
            if name:
                new_arguments[name.async_render()] = value

        result = new_arguments["value"]
        value_template = function_config.get(CONF_VALUE_TEMPLATE)

        if value_template is not None:
            result = value_template.async_render_with_possible_json_value(
                result, None, new_arguments
            )

        return result

    def _async_update_from_rest_data(
        self,
        data: BeautifulSoup,
        sensor_config: dict[str, Any],
        arguments: dict[str, Any],
    ) -> Any:
        """Update state from the rest data."""
        value = self._extract_value(data, sensor_config)
        value_template = sensor_config.get(CONF_VALUE_TEMPLATE)

        if value_template is not None:
            value = value_template.async_render_with_possible_json_value(
                value, None, arguments
            )

        return value

    def _extract_value(self, data: BeautifulSoup, sensor_config: dict[str, Any]) -> Any:
        """Parse the html extraction in the executor."""
        value: str | list[str] | None
        select = sensor_config[scrape.const.CONF_SELECT]
        index = sensor_config.get(scrape.const.CONF_INDEX, 0)
        attr = sensor_config.get(CONF_ATTRIBUTE)
        try:
            if attr is not None:
                value = data.select(select)[index][attr]
            else:
                tag = data.select(select)[index]
                if tag.name in ("style", "script", "template"):
                    value = tag.string
                else:
                    value = tag.text
        except IndexError:
            _LOGGER.warning("Index '%s' not found", index)
            value = None
        except KeyError:
            _LOGGER.warning("Attribute '%s' not found", attr)
            value = None
        _LOGGER.debug("Parsed value: %s", value)
        return value
