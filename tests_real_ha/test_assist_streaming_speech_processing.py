"""Real HA Assist coverage for streaming spoken-response processing."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _install_wire

_RAW_RESPONSE = (
    "**Hello** see https://example.com/path and "
    "[docs](https://example.org/help) done."
)
_SPOKEN_RESPONSE = "Hello see and done."


def _chat_sse_deltas(parts: list[str]) -> bytes:
    """Return one real Chat Completions SSE response split across provider deltas."""
    events: list[str] = []
    for index, content in enumerate(parts):
        delta: dict[str, Any] = {"content": content}
        if index == 0:
            delta["role"] = "assistant"
        chunk = {
            "id": "chatcmpl-assist-streaming-speech",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": "stop" if index == len(parts) - 1 else None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk)}\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


async def _speech_agent(hass: HomeAssistant) -> Any:
    """Create a real conversation entity with progressive speech cleanup enabled."""
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    entry = _make_entry(
        "Assist Streaming Speech",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
            CONF_SPEECH_PROCESSING_ENABLED: True,
            CONF_SPEECH_STRIP_MARKDOWN: True,
            CONF_SPEECH_STRIP_URLS: True,
            CONF_SPEECH_REGEX_REPLACEMENTS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent.entity_id.startswith("conversation.")
    assert agent._attr_supports_streaming is True  # noqa: SLF001
    return agent


async def _run_assist(
    client: Any,
    *,
    pipeline_id: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Run the public HA Assist websocket pipeline and collect its real events."""
    await client.send_json_auto_id(
        {
            "type": "assist_pipeline/run",
            "start_stage": "intent",
            "end_stage": "intent",
            "pipeline": pipeline_id,
            "input": {"text": "Give me the streaming speech test response"},
            "conversation_id": conversation_id,
            "device_id": "assist-streaming-speech-device",
        }
    )

    result = await client.receive_json()
    assert result["success"] is True

    events: list[dict[str, Any]] = []
    async with asyncio.timeout(5):
        while True:
            message = await client.receive_json()
            event = message["event"]
            events.append(event)
            if event["type"] == "run-end":
                return events


def _progressive_text(events: list[dict[str, Any]]) -> str:
    """Concatenate exactly what HA Assist exposed as progressive assistant speech."""
    return "".join(
        content
        for event in events
        if event["type"] == "intent-progress"
        and isinstance(event.get("data"), dict)
        and isinstance(event["data"].get("chat_log_delta"), dict)
        and isinstance(
            content := event["data"]["chat_log_delta"].get("content"), str
        )
    )


def _final_speech(events: list[dict[str, Any]]) -> str:
    """Return the completed IntentResponse speech emitted by the Assist pipeline."""
    intent_end = next(event for event in events if event["type"] == "intent-end")
    return intent_end["data"]["intent_output"]["response"]["speech"]["plain"][
        "speech"
    ]


async def test_actual_assist_stream_sanitizes_split_provider_deltas(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """Actual HA Assist must receive speech-safe deltas while OpenAI is streaming."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})

    # Both unsafe constructs cross provider-delta boundaries. A completed-response
    # cleanup test cannot prove this: HA Assist sees these deltas progressively while
    # the provider stream is still open.
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_deltas(
                [
                    "**Hello** see ht",
                    "tps://example.com/path and [docs](https://exa",
                    "mple.org/help) done.",
                ]
            )
        ],
    )

    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="assist-streaming-speech",
    )

    event_types = [event["type"] for event in events]
    assert event_types[0] == "run-start"
    assert "intent-start" in event_types
    assert "intent-progress" in event_types
    assert "intent-end" in event_types
    assert event_types[-1] == "run-end"

    progressive = _progressive_text(events)
    assert progressive == _SPOKEN_RESPONSE
    assert _final_speech(events) == _SPOKEN_RESPONSE

    # Nothing unsafe may leak into any progressive Assist content event, including
    # partial URL prefixes that existed only while adjacent provider chunks arrived.
    progress_deltas = [
        event["data"]["chat_log_delta"]
        for event in events
        if event["type"] == "intent-progress"
        and isinstance(event.get("data"), dict)
        and isinstance(event["data"].get("chat_log_delta"), dict)
    ]
    assert any(delta.get("role") == "assistant" for delta in progress_deltas)
    assert len([delta for delta in progress_deltas if delta.get("content")]) >= 2
    for delta in progress_deltas:
        content = delta.get("content")
        if not isinstance(content, str):
            continue
        assert "http" not in content
        assert "example.com" not in content
        assert "example.org" not in content
        assert "[docs]" not in content
        assert "**" not in content

    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    assert wire.requests[0]["body"]["stream"] is True

    # The expected final text is deliberately derived from a raw response containing
    # both Markdown and URLs, so this cannot accidentally pass with plain provider
    # output that needed no transformation.
    assert _RAW_RESPONSE != _SPOKEN_RESPONSE
