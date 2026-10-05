"""Provider transport and diagnostics privacy acceptance boundaries."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from aiohttp import web
import httpx
import pytest

from custom_components.extended_openai_conversation_responses import (
    config_flow as config_flow_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _prepare_service,
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
)

_ACTIVE_DIAGNOSTICS_KEY = "sk-active-diagnostics-CANARY-1"
_OLD_ROTATED_KEY = "sk-old-diagnostics-CANARY-2"
_NEW_ROTATED_KEY = "sk-new-diagnostics-CANARY-3"
_REQUEST_MARKER = "PRIVATE_ACTIVE_REQUEST_DIAGNOSTIC_MARKER"


def _reply(mode: str, text: str) -> bytes:
    return (
        _responses_sse_text(text)
        if mode == API_MODE_RESPONSES
        else _chat_sse_text(text)
    )


async def _turn(
    hass: HomeAssistant,
    entry_id: str,
    text: str,
    *,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=entry_id,
    )


def _speech(result: conversation.ConversationResult) -> str:
    return result.response.as_dict()["speech"]["plain"]["speech"]


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/html", b"<html><body>CAPTIVE_PORTAL_ASSISTANT_MARKER</body></html>"),
        ("application/json", b'{"portal":"CAPTIVE_PORTAL_ASSISTANT_MARKER"}'),
        (
            "text/plain",
            b'data: {"type":"CAPTIVE_PORTAL_ASSISTANT_MARKER"}\n\n',
        ),
    ],
    ids=["html", "unrelated-json", "wrong-content-type-sse-lookalike"],
)
@pytest.mark.parametrize(
    "mode",
    [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES],
    ids=["chat", "responses"],
)
async def test_http_200_invalid_provider_body_fails_then_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    content_type: str,
    body: bytes,
) -> None:
    """A successful HTTP status cannot turn a proxy/captive body into assistant text."""
    entry = _make_entry(
        "Invalid successful provider body",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    raw = _raw_client(agent)
    raw.max_retries = 0
    requests: list[httpx.Request] = []

    async def send(
        request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": content_type},
                content=body,
                request=request,
            )
        assert len(requests) == 2
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_reply(mode, "Recovered after invalid provider body."),
            request=request,
        )

    monkeypatch.setattr(raw._client, "send", send)

    failed = await _turn(hass, entry.entry_id, "First request sees invalid provider body")
    assert failed.response.error_code is not None
    assert "CAPTIVE_PORTAL_ASSISTANT_MARKER" not in _speech(failed)
    assert len(requests) == 1

    recovered = await _turn(hass, entry.entry_id, "Second request should recover")
    assert recovered.response.error_code is None
    assert _speech(recovered) == "Recovered after invalid provider body."
    assert len(requests) == 2


async def _start_server(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize(
    "mode",
    [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES],
    ids=["chat", "responses"],
)
async def test_cross_origin_provider_redirect_never_forwards_credentials(
    hass: HomeAssistant,
    socket_enabled: Any,
    status: int,
    mode: str,
) -> None:
    """Cross-origin redirects may fail or follow, but credentials never cross origins."""
    del socket_enabled
    target_requests: list[dict[str, Any]] = []
    source_requests: list[dict[str, Any]] = []

    target_app = web.Application()

    async def target(request: web.Request) -> web.Response:
        target_requests.append(
            {
                "method": request.method,
                "authorization": request.headers.get("authorization"),
                "api_key": request.headers.get("api-key"),
                "path": request.path,
            }
        )
        return web.Response(
            body=_reply(mode, "Redirect target response."),
            content_type="text/event-stream",
        )

    target_app.router.add_route("*", "/redirected", target)
    target_runner, target_root = await _start_server(target_app)

    source_app = web.Application()
    direct_body = _reply(mode, "Healthy request after redirect.")

    async def source(request: web.Request) -> web.Response:
        source_requests.append(
            {
                "authorization": request.headers.get("authorization"),
                "path": request.path,
            }
        )
        if len(source_requests) == 1:
            return web.Response(
                status=status,
                headers={"Location": f"{target_root}/redirected"},
            )
        return web.Response(body=direct_body, content_type="text/event-stream")

    source_app.router.add_post("/v1/chat/completions", source)
    source_app.router.add_post("/v1/responses", source)
    source_runner, source_root = await _start_server(source_app)

    entry = _make_entry(
        "Redirect credential containment",
        include_ai_task=False,
        base_url=f"{source_root}/v1",
        conversation_options={
            CONF_API_MODE: mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
        },
    )
    try:
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        _raw_client(agent).max_retries = 0

        first = await _turn(hass, entry.entry_id, f"Redirect status {status}")
        # Redirect policy belongs to the SDK/HTTP stack. Either a handled failure
        # or a credential-free followed request is acceptable here.
        if first.response.error_code is None:
            assert _speech(first) == "Redirect target response."

        assert len(source_requests) == 1
        assert source_requests[0]["authorization"] == "Bearer sk-acceptance-test"
        for captured in target_requests:
            assert captured["authorization"] is None
            assert captured["api_key"] is None

        second = await _turn(hass, entry.entry_id, "Fresh request after redirect")
        assert second.response.error_code is None
        assert _speech(second) == "Healthy request after redirect."
        assert len(source_requests) == 2
    finally:
        await source_runner.cleanup()
        await target_runner.cleanup()


async def test_diagnostics_during_active_tool_continuation_are_safe_and_non_intrusive(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect diagnostics after a tool effect while provider continuation is held."""
    tool = dict(DEFAULT_CONF_FUNCTION_TOOLS[0])
    entry = _make_entry(
        "Active diagnostics",
        include_ai_task=False,
        data={CONF_API_KEY: _ACTIVE_DIAGNOSTICS_KEY},
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    raw = _raw_client(agent)
    raw.max_retries = 0

    service_calls = await _prepare_service(hass)
    continuation_entered = asyncio.Event()
    release_continuation = asyncio.Event()
    provider_requests: list[dict[str, Any]] = []

    async def send(
        request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        provider_requests.append(json.loads(request.content))
        if len(provider_requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_chat_sse_tool_call(),
                request=request,
            )
        assert len(provider_requests) == 2
        continuation_entered.set()
        await release_continuation.wait()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_text("Active request completed once."),
            request=request,
        )

    monkeypatch.setattr(raw._client, "send", send)
    active = asyncio.create_task(
        _turn(
            hass,
            entry.entry_id,
            f"Turn off the test light. {_REQUEST_MARKER}",
            conversation_id="active-diagnostics-conversation",
        )
    )
    try:
        await asyncio.wait_for(continuation_entered.wait(), 10)
        assert len(provider_requests) == 2
        assert len(service_calls) == 1

        diagnostics = await asyncio.wait_for(
            async_get_config_entry_diagnostics(hass, entry), 10
        )
        serialized = json.dumps(diagnostics, sort_keys=True, default=str)
        assert _ACTIVE_DIAGNOSTICS_KEY not in serialized
        assert _REQUEST_MARKER not in serialized
        assert "authorization" not in serialized.lower()
        assert diagnostics["provider_category"] == "openai"
        assert len(diagnostics["conversation_agents"]) == 1

        # Diagnostics must not settle, retry or replay the held request.
        assert not active.done()
        assert len(provider_requests) == 2
        assert len(service_calls) == 1
    finally:
        release_continuation.set()

    result = await asyncio.wait_for(active, 10)
    assert result.response.error_code is None
    assert _speech(result) == "Active request completed once."
    assert len(provider_requests) == 2
    assert len(service_calls) == 1


async def test_diagnostics_after_native_credential_rotation_redact_old_and_new_secrets(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real HA reauth transition cannot expose either credential generation."""
    entry = _make_entry(
        "Diagnostics credential rotation",
        include_ai_task=False,
        data={CONF_API_KEY: _OLD_ROTATED_KEY},
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
        },
    )
    await _setup_entry(hass, entry)
    original_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert original_agent is not None
    raw = _raw_client(original_agent)
    raw.max_retries = 0

    unauthorized = {
        "error": {
            "message": "credential rotation acceptance rejection",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }

    async def reject(
        request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        assert request.headers.get("authorization") == f"Bearer {_OLD_ROTATED_KEY}"
        return httpx.Response(
            401,
            headers={"content-type": "application/json"},
            json=unauthorized,
            request=request,
        )

    monkeypatch.setattr(raw._client, "send", reject)
    failed = await _turn(hass, entry.entry_id, "Trigger credential rotation")
    assert failed.response.error_code is not None
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
    )
    assert len(flows) == 1
    reauth = flows[0]
    assert reauth["step_id"] == "reauth_confirm"

    authenticate = AsyncMock(return_value=object())
    monkeypatch.setattr(
        config_flow_module,
        "get_authenticated_client",
        authenticate,
    )
    result = await hass.config_entries.flow.async_configure(
        reauth["flow_id"],
        {CONF_API_KEY: _NEW_ROTATED_KEY},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["api_key"] == _NEW_ROTATED_KEY

    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_API_KEY] == _NEW_ROTATED_KEY
    replacement_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement_agent is not None
    assert replacement_agent is not original_agent

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics, sort_keys=True, default=str)
    assert _OLD_ROTATED_KEY not in serialized
    assert _NEW_ROTATED_KEY not in serialized
    assert "credential rotation acceptance rejection" not in serialized
    assert diagnostics["provider_category"] == "openai"
    assert diagnostics["conversation_agents"][0]["selected_model"] == "gpt-5.6"
    assert diagnostics["openai_sdk_version"]
