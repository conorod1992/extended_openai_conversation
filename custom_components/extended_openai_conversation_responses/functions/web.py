"""Web functions for HTTP API calls and HTML scraping."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from http import HTTPStatus
import logging
from typing import Any, cast
import zlib

import aiohttp
from bs4 import BeautifulSoup
import voluptuous as vol

from homeassistant.components import rest, scrape
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_HEADERS,
    CONF_METHOD,
    CONF_NAME,
    CONF_PARAMS,
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
from homeassistant.helpers.template import Template, render_complex
from homeassistant.util.json import JSON_DECODE_EXCEPTIONS, json_loads

from ..const import CONF_PAYLOAD_TEMPLATE
from ..resource_limits import MAX_REMOTE_RESPONSE_BYTES
from .base import Function

_ZSTD_COMPAT: Any = None
try:
    from compression.zstd import (
        ZstdDecompressor as _StdlibZstdDecompressor,
        ZstdError as _StdlibZstdError,
    )
except ImportError:  # Python builds can omit the optional stdlib zstd extension.
    import zstandard as _zstandard_backend

    _ZSTD_COMPAT = _zstandard_backend

_LOGGER = logging.getLogger(__name__)

# Brotli rounds its output buffer to allocation blocks. Keeping each request
# below its first 32 KiB block bounds the overflow allocation to one small block.
_DECODE_CHUNK_BYTES = 16 * 1024


def _response_limit_error(max_bytes: int) -> HomeAssistantError:
    return HomeAssistantError(
        f"Remote response exceeds the configured safety limit of {max_bytes} bytes"
    )


def _decode_brotli(body: bytes, max_bytes: int) -> bytes:
    """Drain bounded output and require a complete Brotli stream."""
    from aiohttp import compression_utils

    if not compression_utils.HAS_BROTLI:
        raise HomeAssistantError("Brotli response decoding is unavailable")
    brotli = compression_utils.brotli
    decoder = brotli.Decompressor()
    if not hasattr(decoder, "can_accept_more_data"):
        raise HomeAssistantError("Brotli response decoding requires a bounded codec")
    decoded = bytearray()
    pending = body
    try:
        while True:
            part = decoder.process(
                pending, min(_DECODE_CHUNK_BYTES, max_bytes + 1 - len(decoded))
            )
            pending = b""
            if len(part) > max_bytes - len(decoded):
                raise _response_limit_error(max_bytes)
            decoded.extend(part)
            if decoder.is_finished():
                return bytes(decoded)
            # Consuming all compressed input does not mean buffered output is
            # exhausted. Drain with empty input until completion or no progress.
            if not part:
                raise aiohttp.ClientPayloadError("Incomplete compressed response")
    except brotli.error as err:
        raise aiohttp.ClientPayloadError("Malformed compressed response") from err


def _decode_zstd(body: bytes, max_bytes: int) -> bytes:
    """Bound each output allocation and validate all concatenated frames."""
    if not body:
        raise aiohttp.ClientPayloadError("Empty compressed response")
    if _ZSTD_COMPAT is not None:
        decoded = bytearray()
        try:
            decoder = _ZSTD_COMPAT.ZstdDecompressor().decompressobj()
            for offset in range(len(body)):
                if decoder.eof:
                    decoder = _ZSTD_COMPAT.ZstdDecompressor().decompressobj()
                # One input byte cannot complete more than one Zstd block.
                # Bound expansion per call and explicitly require frame EOF.
                chunk = decoder.decompress(body[offset : offset + 1])
                if len(chunk) > max_bytes - len(decoded):
                    raise _response_limit_error(max_bytes)
                decoded.extend(chunk)
            if not decoder.eof:
                raise aiohttp.ClientPayloadError("Incomplete compressed response")
        except _ZSTD_COMPAT.ZstdError as err:
            message = str(err).lower()
            reason = (
                "Incomplete compressed response"
                if "src size is incorrect" in message or "unexpected eof" in message
                else "Malformed compressed response"
            )
            raise aiohttp.ClientPayloadError(reason) from err
        return bytes(decoded)

    decoded = bytearray()
    try:
        while body:
            decoder = _StdlibZstdDecompressor()
            pending = body
            while True:
                part = decoder.decompress(
                    pending, min(_DECODE_CHUNK_BYTES, max_bytes + 1 - len(decoded))
                )
                pending = b""
                if len(part) > max_bytes - len(decoded):
                    raise _response_limit_error(max_bytes)
                decoded.extend(part)
                if decoder.eof:
                    body = decoder.unused_data
                    break
                if decoder.needs_input:
                    raise aiohttp.ClientPayloadError("Incomplete compressed response")
    except _StdlibZstdError as err:
        raise aiohttp.ClientPayloadError("Malformed compressed response") from err
    return bytes(decoded)


def _decode_compressed_body(body: bytes, encoding: str, max_bytes: int) -> bytes:
    """Bound accepted decoded content and reject incomplete gzip/deflate streams."""
    if encoding in {"gzip", "deflate"}:
        if not body:
            raise aiohttp.ClientPayloadError("Empty compressed response")
        wbits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        # aiohttp also accepts the legacy raw-deflate representation.
        if encoding == "deflate" and body and body[0] & 0x0F != 8:
            wbits = -zlib.MAX_WBITS
        decoded = bytearray()
        while body:
            decoder = zlib.decompressobj(wbits)
            try:
                decoded.extend(decoder.decompress(body, max_bytes + 1 - len(decoded)))
            except zlib.error as err:
                raise aiohttp.ClientPayloadError(
                    "Malformed compressed response"
                ) from err
            if len(decoded) > max_bytes:
                break
            if not decoder.eof:
                raise aiohttp.ClientPayloadError("Incomplete compressed response")
            body = decoder.unused_data
        result = bytes(decoded)
    elif encoding == "br":
        return _decode_brotli(body, max_bytes)
    elif encoding == "zstd":
        return _decode_zstd(body, max_bytes)
    else:
        return body
    if len(result) > max_bytes:
        raise HomeAssistantError(
            f"Remote response exceeds the configured safety limit of {max_bytes} bytes"
        )
    return result


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
        if not 200 <= self._response.status < 300:
            status = self._response.status
            # Use only a standard reason, never the URL, headers or remote body.
            # Raising here keeps the underlying request's __aexit__ in charge
            # of cleanup, including when HA catches a transport exception.
            try:
                reason = HTTPStatus(status).phrase
            except ValueError:
                reason = "Unsuccessful response"
            raise HomeAssistantError(f"REST request failed: HTTP {status} {reason}")
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

        encoding = self._response.headers.get("Content-Encoding", "").lower().strip()
        if encoding:
            body = await asyncio.to_thread(
                _decode_compressed_body, body, encoding, self._max_bytes
            )
        self._body = body
        return body

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        """Decode the same bounded body that aiohttp would expose as text."""
        body = await self.read()
        selected_encoding = encoding or self._response.charset or "utf-8"
        try:
            return body.decode(selected_encoding, errors=errors)
        except UnicodeDecodeError as err:
            if encoding is None:
                # RestData retries the same cached body with its configured
                # encoding when the server's advertised charset is incorrect.
                raise
            raise HomeAssistantError(
                "Remote response cannot be decoded with the configured encoding"
            ) from err
        except (UnicodeError, LookupError) as err:
            raise HomeAssistantError(
                "Remote response cannot be decoded with the configured encoding"
            ) from err


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
        # Read encoded bytes ourselves: aiohttp accepts a truncated gzip stream
        # as partial success, preventing an effective body-integrity check.
        kwargs["auto_decompress"] = False
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

    for key in (CONF_HEADERS, CONF_PARAMS):
        if key in rendered_config:
            rendered_config[key] = render_complex(
                rendered_config[key], arguments, parse_result=key == CONF_PARAMS
            )

    rest_data = rest.create_rest_data_from_config(hass, rendered_config)
    if isinstance(rest_data, rest.data.RestData):
        _install_bounded_session(hass, rest_data)
    return rest_data


def _render_value(
    value_template: Template, value: Any, arguments: dict[str, Any]
) -> Any:
    """Expose value/value_json while propagating HA template errors."""
    variables = {
        key: item
        for key, item in arguments.items()
        if key not in {"value", "value_json"}
    }
    variables["value"] = value
    with suppress(*JSON_DECODE_EXCEPTIONS):
        variables["value_json"] = json_loads(value)
    # HA's async_render_with_possible_json_value silently returns a fallback
    # on Jinja errors. Tools must surface them through the normal executor.
    return value_template.async_render(variables, parse_result=False)


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

        try:
            await rest_data.async_update()
        except (aiohttp.ClientError, TimeoutError) as transport_error:
            raise HomeAssistantError(
                f"REST request failed: {type(transport_error).__name__}"
            ) from transport_error
        if (error := getattr(rest_data, "last_exception", None)) is not None:
            # Transport exceptions can contain private endpoint URLs/credentials.
            raise HomeAssistantError(
                f"REST request failed: {type(error).__name__}"
            ) from error
        value = rest_data.data_without_xml()
        value_template = function_config.get(CONF_VALUE_TEMPLATE)

        if value is not None and value_template is not None:
            value = _render_value(value_template, value, arguments)

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
            # get_rest_data already resolved resource_template/payload_template.
            # The coordinator owns fetching/parsing, not a second render without
            # Function Tool arguments. Never pass source templates to it again.
            {},
            scrape.const.DEFAULT_SCAN_INTERVAL,
        )
        await coordinator.async_refresh()
        if coordinator.data is None:
            raise HomeAssistantError("Remote scrape response is unavailable")

        new_arguments = dict(arguments)

        for extraction_index, sensor_config in enumerate(function_config["sensor"]):
            name: Template = sensor_config.get(CONF_NAME)
            value = await self._async_update_from_rest_data(
                hass, coordinator.data, sensor_config, arguments, extraction_index
            )
            new_arguments["value"] = value
            if name:
                new_arguments[name.async_render(arguments, parse_result=False)] = value

        result = new_arguments["value"]
        value_template = function_config.get(CONF_VALUE_TEMPLATE)

        if value_template is not None:
            result = _render_value(value_template, result, new_arguments)

        return result

    async def _async_update_from_rest_data(
        self,
        hass: HomeAssistant,
        data: BeautifulSoup,
        sensor_config: dict[str, Any],
        arguments: dict[str, Any],
        extraction_index: int = 0,
    ) -> Any:
        """Extract one sensor value without blocking Home Assistant's event loop."""
        value = await hass.async_add_executor_job(
            self._extract_value, data, sensor_config, extraction_index
        )
        value_template = sensor_config.get(CONF_VALUE_TEMPLATE)

        if value_template is not None:
            value = _render_value(value_template, value, arguments)

        return value

    def _extract_value(
        self,
        data: BeautifulSoup,
        sensor_config: dict[str, Any],
        extraction_index: int = 0,
    ) -> Any:
        """Parse HTML and extract one configured value."""
        value: str | list[str] | None
        select = sensor_config[scrape.const.CONF_SELECT]
        index = sensor_config.get(scrape.const.CONF_INDEX, 0)
        attr = sensor_config.get(CONF_ATTRIBUTE)
        from ..operational_errors import current_function_tool_name

        matches = data.select(select)
        try:
            if attr is not None:
                value = matches[index][attr]
            else:
                tag = matches[index]
                if tag.name in ("style", "script", "template"):
                    value = tag.string
                else:
                    value = tag.text
        except IndexError:
            _LOGGER.warning(
                "Scraper Function Tool %s extraction=%d: index=%s unavailable; matches=%d. "
                "Review this extraction's selector and index in Functions",
                current_function_tool_name(),
                extraction_index,
                index,
                len(matches),
            )
            value = None
        except KeyError:
            _LOGGER.warning(
                "Scraper Function Tool %s extraction=%d: attribute=%s missing at index=%s; matches=%d. "
                "Review this extraction's attribute and selector in Functions",
                current_function_tool_name(),
                extraction_index,
                attr,
                index,
                len(matches),
            )
            value = None
        _LOGGER.debug(
            "Scraper Function Tool %s extraction=%d matches=%d value_available=%s",
            current_function_tool_name(),
            extraction_index,
            len(matches),
            value is not None,
        )
        return value
