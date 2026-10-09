"""Check public configuration diagnostics against an independent model/API contract."""

import json

import httpx
import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.agent_test import (
    async_test_agent,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _raw_client,
    _response_object,
    _responses_sse_text,
)


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("model,effort", [("gpt-4.1", None), ("gpt-6-luna", "none")])
async def test_agent_probe_does_not_reject_working_supported_configuration(
    hass, monkeypatch, model, effort, api_mode
):
    # Public normalization accepts this exact configuration, then HA loads it normally.
    options = normalize_agent_config(
        {
            CONF_CHAT_MODEL: model,
            CONF_API_MODE: api_mode,
            CONF_REASONING_EFFORT: effort,
            CONF_FUNCTION_TOOLS: [],
        }
    )
    agent = await _agent(hass, **options)
    requests = []

    async def send(request, *args, **kwargs):
        body = json.loads(request.content)
        requests.append(body)
        # Official GPT-6 Luna contract: Chat Function calling is available only
        # at reasoning_effort=none; omitted effort defaults to medium.
        if (
            api_mode == "chat_completions"
            and model == "gpt-6-luna"
            and body.get("tools")
            and body.get("reasoning_effort") != "none"
        ):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Function calling requires reasoning_effort=none",
                        "type": "invalid_request_error",
                        "param": "reasoning_effort",
                        "code": "unsupported_value",
                    }
                },
                request=request,
            )
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    _responses_sse_text("OK.")
                    if api_mode == "responses"
                    else _chat_sse_text("OK.")
                ),
                request=request,
            )
        if api_mode == "responses":
            return httpx.Response(
                200, json=_response_object("audit-probe", []), request=request
            )
        return httpx.Response(
            200,
            json={
                "id": "audit-probe",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK."},
                        "finish_reason": "stop",
                    }
                ],
            },
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    normal = await _say(hass, agent, "Reply OK.")
    assert _speech(normal) == "OK."
    assert requests[0].get("tools")
    if effort is not None:
        assert (
            requests[0].get("reasoning", {}).get("effort")
            if api_mode == "responses"
            else requests[0].get("reasoning_effort")
        ) == effort
    probe = await async_test_agent(hass, agent.entry, agent.subentry)
    checks = {check.name: check for check in probe.checks}
    assert checks["Model access"].status == "Passed", (
        "Test agent must not reject a working supported model configuration"
    )
    assert checks["Function calling"].status == "Passed"
    assert len(requests) == 2
    if effort is not None:
        assert (
            requests[1].get("reasoning", {}).get("effort")
            if api_mode == "responses"
            else requests[1].get("reasoning_effort")
        ) == effort
    else:
        assert "reasoning" not in requests[1] and "reasoning_effort" not in requests[1]
