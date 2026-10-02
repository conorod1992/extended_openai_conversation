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
