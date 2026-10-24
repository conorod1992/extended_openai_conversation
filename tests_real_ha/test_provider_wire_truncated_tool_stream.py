"""Real-HA provider-wire acceptance for truncated streamed tool calls."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _prepare_service,
    _responses_sse_text,
    _say,
    _speech,
)


def _chat_sse_partial_tool_call() -> bytes:
    """Return a Chat Completions stream that ends mid-function arguments."""
    chunk = {
        "id": "chatcmpl-truncated-tool",
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
                            "id": "call-truncated-tool",
                            "type": "function",
                            "function": {
                                "name": "execute_services",
                                "arguments": '{"list":[{"domain":"light"',
                            },
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    }
    # Deliberately no terminal finish reason and no [DONE] sentinel.
    return f"data: {json.dumps(chunk)}\n\n".encode()


def _responses_sse_partial_tool_call() -> bytes:
    """Return a Responses stream that starts but never completes a function call."""
    item = {
        "id": "fc-truncated-tool",
        "type": "function_call",
        "call_id": "call-truncated-tool",
        "name": "execute_services",
        "arguments": "",
        "status": "in_progress",
    }
    event = {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": item,
        "sequence_number": 0,
    }
    # Deliberately no output_item.done and no response.completed terminal event.
    return f"data: {json.dumps(event)}\n\n".encode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_mode", "partial_reply", "recovery_reply", "expected_path"),
    [
        (
            API_MODE_CHAT_COMPLETIONS,
            _chat_sse_partial_tool_call,
            lambda: _chat_sse_text("Recovered after truncated Chat Completions stream."),
            "/v1/chat/completions",
        ),
        (
            API_MODE_RESPONSES,
            _responses_sse_partial_tool_call,
            lambda: _responses_sse_text("Recovered after truncated Responses stream."),
            "/v1/responses",
        ),
    ],
)
async def test_truncated_streamed_tool_call_never_executes_and_next_request_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    partial_reply: Any,
    recovery_reply: Any,
    expected_path: str,
) -> None:
    """Partial provider tool data must fail closed before any HA side effect."""
    agent = await _agent(hass, api_mode)
    service_calls = await _prepare_service(hass)
    wire = _install_wire(
        monkeypatch,
        agent,
        [partial_reply(), recovery_reply()],
    )

    failed = await _say(hass, agent)

    assert failed.response.error_code is not None
    failed_speech = failed.response.as_dict()["speech"]["plain"]["speech"]
    assert "stream ended before" in failed_speech
    assert service_calls == []
    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == expected_path

    # A malformed/truncated provider stream must not poison the agent. A normal
    # subsequent provider response on the same loaded runtime should succeed, and
    # the partial tool call from the previous request must still never execute.
    recovered = await _say(hass, agent)

    assert _speech(recovered).startswith("Recovered after truncated")
    assert service_calls == []
    assert [request["path"] for request in wire.requests] == [
        expected_path,
        expected_path,
    ]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_duplicate_call_ids_recover_trailing_usage_without_executing(
    hass, monkeypatch, mode
):
    from tests_real_ha.test_provider_wire_e2e import _response_object
    from custom_components.extended_openai_conversation_responses import (
        context_usage_hardening as hardening,
    )

    # This is a wire/accounting test, not a wall-clock benchmark: the real SDK's
    # cold Responses model construction can exceed 200 ms on shared CI runners.
    # Dedicated tests enforce the production deadline, event limit, cancellation
    # and rejection of late accounting independently of SDK parsing speed.
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 5)
    arguments = json.dumps(
        {
            "list": [
                {
                    "domain": "light",
                    "service": "turn_off",
                    "service_data": {"entity_id": ["light.provider_wire"]},
                }
            ]
        }
    )
    if mode == "chat_completions":
        usage = {
            "prompt_tokens": 223,
            "completion_tokens": 17,
            "total_tokens": 240,
            "prompt_tokens_details": {"cached_tokens": 11},
            "completion_tokens_details": {"reasoning_tokens": 3},
        }
        base = {
            "id": "duplicate",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
        }
        events = [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": "same-call",
                                    "type": "function",
                                    "function": {
                                        "name": "execute_services",
                                        "arguments": arguments,
                                    },
                                }
                                for i in range(2)
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
            {**base, "choices": [], "usage": usage},
        ]
        stream = (
            "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
            + b"data: [DONE]\n\n"
        )
    else:
        items = [
            {
                "id": f"fc-{i}",
                "type": "function_call",
                "call_id": "same-call",
                "name": "execute_services",
                "arguments": arguments,
                "status": "completed",
            }
            for i in range(2)
        ]
        events = []
        for i, item in enumerate(items):
            events.extend(
                [
                    {
                        "type": "response.output_item.added",
                        "output_index": i,
                        "item": {**item, "arguments": "", "status": "in_progress"},
                        "sequence_number": i * 2,
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": i,
                        "item": item,
                        "sequence_number": i * 2 + 1,
                    },
                ]
            )
        response = _response_object("duplicate", items)
        response["usage"] = {
            "input_tokens": 223,
            "output_tokens": 17,
            "total_tokens": 240,
            "input_tokens_details": {"cached_tokens": 11},
            "output_tokens_details": {"reasoning_tokens": 3},
        }
        events.append(
            {"type": "response.completed", "response": response, "sequence_number": 4}
        )
        stream = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    agent = await _agent(hass, mode)
    calls = await _prepare_service(hass)
    text = _responses_sse_text if mode == "responses" else _chat_sse_text
    _install_wire(monkeypatch, agent, [stream, text("Recovered")])
    failed = await _say(hass, agent)
    assert failed.response.error_code is not None
    assert calls == []
    request = agent._usage.requests[-1]
    assert not request.successful
    assert (request.input_tokens, request.output_tokens, request.total_tokens) == (
        223,
        17,
        240,
    )
    assert (request.cached_input_tokens, request.reasoning_tokens) == (11, 3)
    run = agent._usage.runs[-1]
    assert (run.request_count, run.failed_request_count, run.total_tokens) == (
        1,
        1,
        240,
    )
    from tests_real_ha.test_cross_feature_acceptance import _say as say_followup

    recovered = await say_followup(hass, agent, "continue", failed.conversation_id)
    assert _speech(recovered) == "Recovered"
    assert calls == []
