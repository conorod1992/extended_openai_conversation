"""EOAI stream cancellation and pool ownership over actual loopback sockets."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import ipaddress
import json
from pathlib import Path
import ssl
from typing import Any

from aiohttp import ClientSession, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import helpers
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _prepare_service,
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
    _tool_result_from_chat_request,
    _tool_result_from_responses_request,
)
from tests_stress.conftest import record
from tests_stress.test_active_provider_stream_cancellation import _partial_text


async def _say(
    hass: HomeAssistant, entry_id: str, marker: str
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=marker,
        conversation_id=None,
        context=Context(user_id="real-pool-owner"),
        language="en",
        agent_id=entry_id,
    )


@pytest.mark.parametrize("mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_real_pool_wait_and_cancellation_release_sdk_stream(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    socket_enabled: Any,
    stress_trace: list[dict],
    mode: str,
) -> None:
    del socket_enabled
    MockUser(id="real-pool-owner", name="Pool owner", is_owner=True).add_to_hass(hass)
    arrived: list[str] = []
    stream_open = asyncio.Event()
    release_server = asyncio.Event()
    first, rest = _partial_text(mode)

    async def provider(request: web.Request) -> web.StreamResponse:
        assert request.path == (
            "/v1/responses" if mode == API_MODE_RESPONSES else "/v1/chat/completions"
        )
        body = await request.json()
        serialized = json.dumps(body)
        marker = next(
            marker
            for marker in ("Held", "CancelledWaiter", "Queued", "Healthy", "PostReload")
            if marker in serialized
        )
        arrived.append(marker)
        if marker != "Held":
            payload = (
                _responses_sse_text(marker)
                if mode == API_MODE_RESPONSES
                else _chat_sse_text(marker)
            )
            return web.Response(body=payload, content_type="text/event-stream")
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(first)
        stream_open.set()
        await release_server.wait()
        with suppress(ConnectionResetError):
            await response.write(rest)
            await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", provider)
    app.router.add_post("/v1/responses", provider)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    attempted = {
        marker: asyncio.Event()
        for marker in ("CancelledWaiter", "Queued", "PostReload")
    }

    async def mark_attempt(request: httpx.Request) -> None:
        body = json.dumps(json.loads(request.content))
        for marker, event in attempted.items():
            if marker in body:
                event.set()

    client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        timeout=httpx.Timeout(10.0, pool=10.0),
        trust_env=False,
        event_hooks={"request": [mark_attempt]},
    )
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: client)
    entry = _make_entry(
        "Real provider pool",
        include_ai_task=False,
        base_url=url,
        conversation_options={CONF_API_MODE: mode, CONF_CHAT_MODEL: "gpt-5.6"},
    )
    active: asyncio.Task[Any] | None = None
    cancelled_waiter: asyncio.Task[Any] | None = None
    queued: asyncio.Task[Any] | None = None
    try:
        await _setup_entry(hass, entry)
        active = asyncio.create_task(_say(hass, entry.entry_id, "Held"))
        await asyncio.wait_for(stream_open.wait(), 10)
        cancelled_waiter = asyncio.create_task(
            _say(hass, entry.entry_id, "CancelledWaiter")
        )
        await asyncio.wait_for(attempted["CancelledWaiter"].wait(), 10)
        assert arrived == ["Held"]
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled_waiter, 10)
        queued = asyncio.create_task(_say(hass, entry.entry_id, "Queued"))
        await asyncio.wait_for(attempted["Queued"].wait(), 10)
        assert arrived == ["Held"]
        assert not queued.done()
        hass.states.async_set("sensor.real_pool_probe", "responsive")
        assert hass.states.get("sensor.real_pool_probe").state == "responsive"
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, 10)
        assert _speech(await asyncio.wait_for(queued, 10)) == "Queued"
        assert (
            _speech(await asyncio.wait_for(_say(hass, entry.entry_id, "Healthy"), 10))
            == "Healthy"
        )
        assert arrived == ["Held", "Queued", "Healthy"]

        # A config-entry reload replaces the agent while the old agent still owns
        # a live stream. Cancelling that stale work frees the shared HTTP pool for
        # the replacement agent without requiring the provider to finish the stream.
        stream_open.clear()
        old_agent = conversation.async_get_agent(hass, entry.entry_id)
        active = asyncio.create_task(_say(hass, entry.entry_id, "Held"))
        await asyncio.wait_for(stream_open.wait(), 10)
        assert await asyncio.wait_for(
            hass.config_entries.async_reload(entry.entry_id), 10
        )
        assert entry.state is ConfigEntryState.LOADED
        assert conversation.async_get_agent(hass, entry.entry_id) is not old_agent
        queued = asyncio.create_task(_say(hass, entry.entry_id, "PostReload"))
        await asyncio.wait_for(attempted["PostReload"].wait(), 10)
        assert arrived == ["Held", "Queued", "Healthy", "Held"]
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, 10)
        assert _speech(await asyncio.wait_for(queued, 10)) == "PostReload"
        assert arrived[-1] == "PostReload"
        record(stress_trace, "real_connection_pool", mode=mode, arrivals=arrived)
    finally:
        release_server.set()
        for task in (active, cancelled_waiter, queued):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await client.aclose()
        await runner.cleanup()


def _local_tls(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext, Path]:
    """Generate a private loopback CA; never disable certificate verification."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EOAI loopback")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "loopback.pem", tmp_path / "loopback.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(), cert_path


async def _local_endpoint(handler, *, tls=None):
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    app.router.add_post("/v1/responses", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"{'https' if tls else 'http'}://127.0.0.1:{port}/v1"


@pytest.mark.parametrize("mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_verified_https_buffered_sse_and_idle_close_recover(
    hass,
    monkeypatch,
    socket_enabled,
    tmp_path,
    stress_trace,
    mode,
):
    """A real reverse proxy buffers a fragmented upstream and closes idle TLS."""
    del socket_enabled
    MockUser(id="real-pool-owner", name="TLS owner", is_owner=True).add_to_hass(hass)
    server_tls, verifier, cert_path = await hass.async_add_executor_job(
        _local_tls, tmp_path
    )
    upstream_bodies, transports = [], []
    idle_closed = asyncio.Event()
    payload = (
        _responses_sse_text("Buffered audio-ready reply")
        if mode == API_MODE_RESPONSES
        else _chat_sse_text("Buffered audio-ready reply")
    )

    async def upstream(request):
        upstream_bodies.append(await request.json())
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        for start in range(0, len(payload), 47):
            await response.write(payload[start : start + 47])
            await asyncio.sleep(0.005)
        await response.write_eof()
        return response

    upstream_runner, upstream_url = await _local_endpoint(upstream)
    proxy_client = ClientSession(trust_env=False)

    async def proxy(request):
        transports.append(request.transport)
        async with proxy_client.post(
            upstream_url + request.path.removeprefix("/v1"), json=await request.json()
        ) as upstream_response:
            buffered = await upstream_response.read()
        assert buffered == payload  # Real buffering, rather than an exception shim.
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        for start in range(0, len(buffered), 61):
            await response.write(buffered[start : start + 61])
            await asyncio.sleep(0.005)
        await response.write_eof()
        if len(transports) == 1:

            def close_idle():
                transports[0].close()
                idle_closed.set()

            asyncio.get_running_loop().call_later(0.05, close_idle)
        return response

    proxy_runner, proxy_url = await _local_endpoint(proxy, tls=server_tls)
    # An intentionally rejected certificate also closes the peer's incoming
    # handshake. Account for only that server socket; retain HA's strict handler
    # for every unrelated background exception and all later trusted traffic.
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    rejecting_certificate = True
    handshake_errors = []
    proxy_port = int(proxy_url.split(":")[2].split("/")[0])

    def account_rejected_handshake(active_loop, context):
        transport = context.get("transport")
        address = transport.get_extra_info("sockname") if transport else None
        if (
            rejecting_certificate
            and address
            and address[1] == proxy_port
            and context.get("message")
            == "Error on transport creation for incoming connection"
            and isinstance(
                context.get("exception"), (ConnectionResetError, ssl.SSLError)
            )
        ):
            handshake_errors.append(type(context["exception"]).__name__)
            return
        if previous_handler:
            previous_handler(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(account_rejected_handshake)
    client = httpx.AsyncClient(verify=verifier, trust_env=False, timeout=10)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: client)
    entry = _make_entry(
        "TLS proxy",
        include_ai_task=False,
        base_url=proxy_url,
        conversation_options={CONF_API_MODE: mode, CONF_CHAT_MODEL: "gpt-5.6"},
    )
    try:
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        _raw_client(agent).max_retries = 0
        failed = await _say(hass, entry.entry_id, "Untrusted certificate")
        assert failed.response.error_code is not None
        assert upstream_bodies == [] and transports == []
        await asyncio.sleep(0.05)  # Let the rejected server handshake finish.
        rejecting_certificate = False
        assert len(handshake_errors) <= 1
        # Add this CA to the same live client after the failed handshake. A new
        # Assist request must recover through a genuinely verified TLS connection.
        await hass.async_add_executor_job(
            verifier.load_verify_locations, str(cert_path)
        )
        assert (
            _speech(await _say(hass, entry.entry_id, "Trusted proxy"))
            == "Buffered audio-ready reply"
        )
        await asyncio.wait_for(idle_closed.wait(), 5)
        assert (
            _speech(await _say(hass, entry.entry_id, "After idle close"))
            == "Buffered audio-ready reply"
        )
        assert len(upstream_bodies) == 2 and len(transports) == 2
        assert transports[0] is not transports[1]
        assert "Trusted proxy" in json.dumps(upstream_bodies[0])
        assert "After idle close" in json.dumps(upstream_bodies[1])
        record(
            stress_trace,
            "verified_https_proxy",
            mode=mode,
            tls_verified_requests=2,
            provider_requests=2,
            certificate_rejections=1,
            accounted_peer_handshake_errors=len(handshake_errors),
            buffered_sse_responses=2,
            idle_connection_recoveries=1,
        )
    finally:
        loop.set_exception_handler(previous_handler)
        await client.aclose()
        await proxy_runner.cleanup()
        await proxy_client.close()
        await upstream_runner.cleanup()


@pytest.mark.parametrize("mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_https_socket_break_after_tool_preserves_once_only_actions(
    hass,
    monkeypatch,
    socket_enabled,
    tmp_path,
    stress_trace,
    mode,
):
    """A severed continuation cannot replay a committed real HA service action."""
    del socket_enabled
    MockUser(id="real-pool-owner", name="Socket owner", is_owner=True).add_to_hass(hass)
    server_tls, verifier, cert_path = await hass.async_add_executor_job(
        _local_tls, tmp_path
    )
    await hass.async_add_executor_job(verifier.load_verify_locations, str(cert_path))
    calls = await _prepare_service(hass)
    bodies = []
    reply = _responses_sse_text if mode == API_MODE_RESPONSES else _chat_sse_text
    tool = (
        _responses_sse_tool_call if mode == API_MODE_RESPONSES else _chat_sse_tool_call
    )
    replies = [
        tool(),
        None,
        tool(),
        reply("Recovered after rejected replay."),
        tool(call_id="call-socket-new-request"),
        reply("Completed a new request."),
    ]

    async def provider(request):
        bodies.append(await request.json())
        index = len(bodies) - 1
        assert index < len(replies)
        if index != 1:
            return web.Response(body=replies[index], content_type="text/event-stream")
        tool_result = (
            _tool_result_from_responses_request
            if mode == API_MODE_RESPONSES
            else _tool_result_from_chat_request
        )(bodies[index])
        assert tool_result["result"][0]["success"] is True
        assert len(calls) == 1
        first, _ = _partial_text(mode)
        response = web.StreamResponse(
            headers={
                "content-type": "text/event-stream",
                "content-length": str(len(first) + 1000),
            }
        )
        await response.prepare(request)
        await response.write(first)
        await asyncio.sleep(0.01)
        assert request.transport is not None
        request.transport.abort()  # Actual TLS/TCP break with an incomplete body.
        return response

    runner, url = await _local_endpoint(provider, tls=server_tls)
    client = httpx.AsyncClient(verify=verifier, trust_env=False, timeout=10)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: client)
    entry = _make_entry(
        "TLS tool recovery",
        include_ai_task=False,
        base_url=url,
        conversation_options={
            CONF_API_MODE: mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
        },
    )
    try:
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        _raw_client(agent).max_retries = 0

        async def turn(text):
            return await conversation.async_converse(
                hass=hass,
                text=text,
                conversation_id=f"real-socket-{mode}",
                context=Context(user_id="real-pool-owner"),
                language="en",
                agent_id=entry.entry_id,
            )

        failed = await turn("Turn off the test light")
        assert failed.response.error_code is not None and len(calls) == 1
        replay = await turn("Retry the interrupted request")
        assert replay.response.error_code is not None and len(calls) == 1
        assert (
            _speech(await turn("Report status")) == "Recovered after rejected replay."
        )
        assert len(calls) == 1
        assert (
            _speech(await turn("Turn off the test light again"))
            == "Completed a new request."
        )
        assert len(calls) == 2 and len(bodies) == 6
        record(
            stress_trace,
            "https_tool_socket_recovery",
            mode=mode,
            ha_service_calls=2,
            provider_requests=len(bodies),
            transport_tool_recovery_cases=1,
            duplicate_side_effects=0,
            public_turns=4,
        )
    finally:
        await client.aclose()
        await runner.cleanup()
