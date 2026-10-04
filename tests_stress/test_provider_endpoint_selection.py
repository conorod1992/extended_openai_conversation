"""Saved endpoints select genuine installed SDK clients and authenticated wire."""

import json

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_SKIP_AUTHENTICATION,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context
from tests_real_ha.test_config_flow import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _raw_client, _speech
from tests_stress.conftest import record


@pytest.mark.parametrize(
    "endpoint,provider,azure,path",
    [
        (
            "https://EXAMPLE.OPENAI.AZURE.COM",
            "openai",
            True,
            "/openai/deployments/gpt-5.6/chat/completions",
        ),
        (
            "https://proxy.example/upstream/example.openai.azure.com/v1",
            "openai",
            False,
            "/upstream/example.openai.azure.com/v1/chat/completions",
        ),
        (
            "https://example.openai.azure.com.proxy.example/v1",
            "openai",
            False,
            "/v1/chat/completions",
        ),
        (
            "https://explicit-proxy.example",
            "azure",
            True,
            "/openai/deployments/gpt-5.6/chat/completions",
        ),
    ],
)
async def test_saved_endpoint_selects_sdk_and_provider_wire(
    hass, monkeypatch, stress_trace, endpoint, provider, azure, path
):
    entry = _make_entry(
        "Endpoint selection",
        data={
            CONF_API_KEY: "controlled-endpoint-key",
            CONF_BASE_URL: endpoint,
            CONF_API_PROVIDER: provider,
            CONF_API_VERSION: "2025-01-01-preview",
            CONF_SKIP_AUTHENTICATION: True,
        },
        conversation_data={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    sdk = _raw_client(agent)
    requests = []

    async def controlled_send(request, *args, **kwargs):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_text("Endpoint journey completed."),
            request=request,
        )

    # Keep installed SDK serialization, endpoint/deployment routing and SSE parsing.
    monkeypatch.setattr(sdk._client, "send", controlled_send)
    try:
        assert isinstance(sdk, AsyncAzureOpenAI if azure else AsyncOpenAI)
        assert isinstance(sdk, AsyncAzureOpenAI) is azure
        result = await conversation.async_converse(
            hass=hass,
            text="Verify saved endpoint",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.error_code is None
        assert _speech(result) == "Endpoint journey completed."
        assert len(requests) == 1
        request = requests[0]
        assert request.url.path == path
        assert request.url.host == httpx.URL(endpoint).host
        assert json.loads(request.content)["model"] == "gpt-5.6"
        if azure:
            assert request.url.params["api-version"] == "2025-01-01-preview"
            assert request.headers["api-key"] == "controlled-endpoint-key"
            assert "authorization" not in request.headers
        else:
            assert "api-version" not in request.url.params
            assert request.headers["authorization"] == "Bearer controlled-endpoint-key"
            assert "api-key" not in request.headers
        record(
            stress_trace,
            "saved_endpoint_wire",
            sdk=type(sdk).__name__,
            host=request.url.host,
            path=path,
            azure=azure,
        )
        record(
            stress_trace,
            "summary",
            provider_endpoint_wire_cases=1,
            layer="provider-wire",
        )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "endpoint,provider,azure,path",
    [
        (
            "https://example.openai.azure.com",
            "openai",
            True,
            "/openai/deployments/gpt-5.6/chat/completions",
        ),
        (
            "https://compatible.example/v1",
            "openai",
            False,
            "/v1/chat/completions",
        ),
    ],
)
async def test_provider_family_keeps_tool_continuation_on_selected_sdk_wire(
    hass, monkeypatch, stress_trace, endpoint, provider, azure, path
):
    """Endpoint selection must survive a genuine multi-request tool exchange."""
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOLS,
        DEFAULT_CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from tests_real_ha.test_acceptance_lifecycle import (
        _make_entry as _make_wire_entry,
        _setup_entry as _setup_wire_entry,
    )
    from tests_real_ha.test_provider_wire_e2e import (
        _TOOL_CALL_ID,
        _chat_sse_tool_call,
        _tool_result_from_chat_request,
    )

    entity_id = "light.endpoint_tool_probe"
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    entry = _make_wire_entry(
        "Endpoint tool continuation",
        include_ai_task=False,
        base_url=endpoint,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
        },
        data={
            CONF_API_KEY: "controlled-endpoint-key",
            CONF_BASE_URL: endpoint,
            CONF_API_PROVIDER: provider,
            CONF_API_VERSION: "2025-01-01-preview",
            CONF_SKIP_AUTHENTICATION: True,
        },
    )
    await _setup_wire_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    sdk = _raw_client(agent)
    hass.states.async_set(entity_id, "on")
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    effects = []

    async def turn_off(call):
        effects.append((call.domain, call.service, dict(call.data)))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    requests = []

    async def controlled_send(request, *args, **kwargs):
        del args, kwargs
        body = json.loads(request.content)
        requests.append((request, body))
        if len(requests) == 1:
            advertised = {item["function"]["name"] for item in body.get("tools", [])}
            assert "execute_services" in advertised
            payload = _chat_sse_tool_call(
                _TOOL_CALL_ID,
                "execute_services",
                {
                    "list": [
                        {
                            "domain": "light",
                            "service": "turn_off",
                            "service_data": {"entity_id": [entity_id]},
                        }
                    ]
                },
            )
        else:
            assert len(requests) == 2
            tool_messages = [
                item for item in body["messages"] if item.get("role") == "tool"
            ]
            assert len(tool_messages) == 1
            assert tool_messages[0]["tool_call_id"] == _TOOL_CALL_ID
            payload = _chat_sse_text("Endpoint tool completed.")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    monkeypatch.setattr(sdk._client, "send", controlled_send)
    try:
        result = await conversation.async_converse(
            hass=hass,
            text="Turn off the endpoint probe",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.error_code is None
        assert _speech(result) == "Endpoint tool completed."
        tool_result = _tool_result_from_chat_request(requests[1][1])
        assert effects == [("light", "turn_off", {"entity_id": [entity_id]})], (
            f"provider returned tool result {tool_result!r}"
        )
        assert hass.states.get(entity_id).state == "off"
        assert len(requests) == 2
        assert all(request.url.path == path for request, _body in requests)
        assert all(
            request.url.host == httpx.URL(endpoint).host for request, _body in requests
        )
        for request, body in requests:
            assert body["model"] == "gpt-5.6"
            if azure:
                assert request.url.params["api-version"] == "2025-01-01-preview"
                assert request.headers["api-key"] == "controlled-endpoint-key"
                assert "authorization" not in request.headers
            else:
                assert "api-version" not in request.url.params
                assert (
                    request.headers["authorization"] == "Bearer controlled-endpoint-key"
                )
                assert "api-key" not in request.headers
        record(
            stress_trace,
            "summary",
            provider_family_tool_continuations=1,
            provider_family_wire_requests=2,
            actual_tool_executions=1,
        )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
