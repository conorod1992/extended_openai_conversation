"""Strict IPv6-only acceptance through real provider and REST Function Tool sockets."""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
import socket
from typing import Any
from urllib.parse import urlparse

from aiohttp import web
import pytest
import yaml

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_IPV6_ONLY_ACCEPTANCE") != "1",
    reason="requires strict IPv6-only acceptance lane",
)

_TOOL_NAME = "ipv6_rest_probe"
_MARKER = "IPV6_REST_TOOL_OK"


def _reserve_ipv6_port() -> int:
    """Reserve an ephemeral port specifically in the IPv6 socket namespace."""
    with closing(socket.socket(socket.AF_INET6, socket.SOCK_STREAM)) as sock:
        sock.bind(("::1", 0))
        return int(sock.getsockname()[1])


def _assert_no_ipv4_listener(port: int) -> None:
    """Prove the tested endpoint cannot succeed through IPv4 fallback."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.5)
        assert sock.connect_ex(("127.0.0.1", port)) != 0, (
            "IPv4 unexpectedly reached the IPv6 acceptance endpoint"
        )


def _tool_call_sse() -> bytes:
    chunk = {
        "id": "chatcmpl-ipv6-tool",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-ipv6-rest",
                            "type": "function",
                            "function": {
                                "name": _TOOL_NAME,
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _text_sse(text: str) -> bytes:
    chunk = {
        "id": "chatcmpl-ipv6-text",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


class IPv6OnlyEndpoint:
    """One IPv6-only server used as both provider and REST Function Tool target."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.runner: web.AppRunner | None = None
        self.provider_requests: list[dict[str, Any]] = []
        self.rest_requests: list[dict[str, Any]] = []
        self.provider_failures_remaining = 0

    def _connection_evidence(self, request: web.Request) -> dict[str, Any]:
        transport = request.transport
        assert transport is not None
        sock = transport.get_extra_info("socket")
        peer = transport.get_extra_info("peername")
        local = transport.get_extra_info("sockname")
        assert sock is not None
        assert sock.family == socket.AF_INET6
        assert peer is not None and len(peer) == 4
        assert local is not None and len(local) == 4
        assert local[0] == "::1"
        return {
            "family": "AF_INET6",
            "peer": peer[0],
            "local": local[0],
            "host": request.host,
        }

    async def _provider(self, request: web.Request) -> web.StreamResponse:
        connection = self._connection_evidence(request)
        body = await request.json()
        self.provider_requests.append({"connection": connection, "body": body})

        if self.provider_failures_remaining:
            self.provider_failures_remaining -= 1
            return web.json_response(
                {
                    "error": {
                        "message": "controlled IPv6 provider failure",
                        "type": "server_error",
                    }
                },
                status=503,
            )

        serialized = json.dumps(body)
        payload = (
            _text_sse("IPv6 provider and REST tool both succeeded.")
            if _MARKER in serialized
            else _tool_call_sse()
        )
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await response.prepare(request)
        # Split each SSE payload so the SDK must consume a genuinely streamed
        # response over the IPv6 socket rather than one buffered HTTP body.
        split = max(1, len(payload) // 3)
        for offset in range(0, len(payload), split):
            await response.write(payload[offset : offset + split])
            await asyncio.sleep(0)
        await response.write_eof()
        return response

    async def _fact(self, request: web.Request) -> web.Response:
        connection = self._connection_evidence(request)
        self.rest_requests.append({"connection": connection, "path": request.path})
        return web.json_response({"marker": _MARKER})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._provider)
        app.router.add_get("/fact", self._fact)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "::1", self.port)
        await site.start()

        sockets = getattr(site._server, "sockets", None)  # noqa: SLF001 - test socket
        assert sockets and len(sockets) == 1
        assert sockets[0].family == socket.AF_INET6
        assert sockets[0].getsockname()[0] == "::1"
        _assert_no_ipv4_listener(self.port)

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


def _rest_tool(endpoint: str) -> dict[str, Any]:
    return {
        "spec": {
            "name": _TOOL_NAME,
            "description": "Read one deterministic marker over IPv6.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "rest",
            "resource": endpoint,
            "method": "GET",
            "timeout": 10,
            "verify_ssl": False,
        },
        "enabled": True,
    }


async def _say(
    hass: HomeAssistant,
    entry_id: str,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


async def test_ipv6_only_provider_stream_rest_tool_and_failure_recovery(
    hass: HomeAssistant,
) -> None:
    """EOAI must work when every tested remote endpoint is reachable only over IPv6."""
    port = _reserve_ipv6_port()
    endpoint = IPv6OnlyEndpoint(port)
    await endpoint.start()
    try:
        base_url = f"http://[::1]:{port}/v1"
        rest_url = f"http://[::1]:{port}/fact"

        for url in (base_url, rest_url):
            parsed = urlparse(url)
            assert parsed.hostname == "::1"
            assert parsed.port == port
            assert parsed.netloc.startswith("[::1]:")

        entry = _make_entry(
            "IPv6-only acceptance",
            include_ai_task=False,
            base_url=base_url,
            conversation_options={
                CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
                CONF_CHAT_MODEL: "gpt-5.6",
                CONF_FUNCTION_TOOLS: yaml.safe_dump(
                    [_rest_tool(rest_url)],
                    sort_keys=False,
                    allow_unicode=True,
                ),
            },
        )
        await _setup_entry(hass, entry)

        result = await _say(
            hass,
            entry.entry_id,
            "Use the IPv6 REST probe and report when it succeeds.",
        )
        assert result.response.error_code is None
        assert (
            result.response.as_dict()["speech"]["plain"]["speech"]
            == "IPv6 provider and REST tool both succeeded."
        )

        assert len(endpoint.provider_requests) == 2
        assert len(endpoint.rest_requests) == 1
        assert _MARKER in json.dumps(endpoint.provider_requests[1]["body"])
        assert all(
            item["connection"]["family"] == "AF_INET6"
            and item["connection"]["local"] == "::1"
            and item["connection"]["host"].startswith("[::1]:")
            for item in [*endpoint.provider_requests, *endpoint.rest_requests]
        )
        _assert_no_ipv4_listener(port)

        # Exercise a transport failure and then a fresh conversation. The first
        # request must fail without creating an IPv4 escape hatch; the next request
        # must reconnect successfully to the same IPv6-only provider.
        # OpenAI's SDK retries transient failures internally. Three consecutive
        # failures exhaust its normal retry budget, so this conversation genuinely
        # crosses the user-visible failure boundary before the next one reconnects.
        endpoint.provider_failures_remaining = 3
        failed = await _say(hass, entry.entry_id, "Trigger the controlled failure.")
        assert failed.response.error_code is not None
        provider_count_after_failure = len(endpoint.provider_requests)

        recovered = await _say(
            hass,
            entry.entry_id,
            "Use the IPv6 REST probe again after the failure.",
        )
        assert recovered.response.error_code is None
        assert (
            recovered.response.as_dict()["speech"]["plain"]["speech"]
            == "IPv6 provider and REST tool both succeeded."
        )
        assert len(endpoint.provider_requests) == provider_count_after_failure + 2
        assert len(endpoint.rest_requests) == 2
        _assert_no_ipv4_listener(port)
    finally:
        await endpoint.close()
