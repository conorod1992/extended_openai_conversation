"""Nightly malformed provider event ordering and duplicate-event safety."""

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
    _TOOL_ARGUMENTS,
    _TOOL_CALL_ID,
    _agent,
    _chat_sse_text,
    _install_wire,
    _prepare_service,
    _response_object,
    _responses_sse_text,
    _say,
    _speech,
)
from tests_stress.conftest import record


def _duplicate_chat_tool_stream() -> bytes:
    chunk = {
        "id": "chatcmpl-duplicate-tool",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [{
            "index": 0,
            "delta": {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": _TOOL_CALL_ID,
                    "type": "function",
                    "function": {
                        "name": "execute_services",
                        "arguments": json.dumps(_TOOL_ARGUMENTS, separators=(",", ":")),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    encoded = f"data: {json.dumps(chunk)}\n\n"
    return f"{encoded}{encoded}data: [DONE]\n\n".encode()


def _duplicate_responses_tool_stream() -> bytes:
    arguments = json.dumps(_TOOL_ARGUMENTS, separators=(",", ":"))
    item = {
        "id": "fc-duplicate-provider-wire",
        "type": "function_call",
        "call_id": _TOOL_CALL_ID,
        "name": "execute_services",
        "arguments": arguments,
        "status": "completed",
    }
    done = {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": item,
        "sequence_number": 1,
    }
    completed = {
        "type": "response.completed",
        "response": _response_object("resp-duplicate-provider-wire", [item]),
        "sequence_number": 2,
    }
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "arguments": "", "status": "in_progress"},
            "sequence_number": 0,
        },
        done,
        {**done, "sequence_number": 2},
        {**completed, "sequence_number": 3},
        {**completed, "sequence_number": 4},
    ]
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


@pytest.mark.parametrize(
    ("api_mode", "bad_stream", "recovery"),
    [
        (
            API_MODE_CHAT_COMPLETIONS,
            _duplicate_chat_tool_stream(),
            _chat_sse_text("Provider recovered after duplicate events."),
        ),
        (
            API_MODE_RESPONSES,
            _duplicate_responses_tool_stream(),
            _responses_sse_text("Provider recovered after duplicate events."),
        ),
    ],
)
@pytest.mark.asyncio
async def test_duplicate_or_contradictory_events_never_repeat_side_effects(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    bad_stream: bytes,
    recovery: bytes,
    stress_trace: list[dict],
) -> None:
    """Malformed duplicate event sequences may fail, but never duplicate actions."""
    agent = await _agent(hass, api_mode)
    assert agent is not None
    calls = await _prepare_service(hass)
    wire = _install_wire(
        monkeypatch,
        agent,
        [bad_stream, recovery, recovery],
    )

    first = await _say(hass, agent)
    assert len(calls) <= 1, "duplicate provider events repeated a Home Assistant action"

    second = await _say(hass, agent)
    assert _speech(second) == "Provider recovered after duplicate events."
    assert len(calls) <= 1
    assert len(wire.requests) in {2, 3}

    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        contradictory_stream_sequences=1,
        provider_requests=len(wire.requests),
        ha_service_calls=len(calls),
        first_turn_failed=first.response.error_code is not None,
        api_mode=api_mode,
    )
