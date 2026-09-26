"""Nightly speech streaming through the public Home Assistant Assist pipeline."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import random
from typing import Any

import httpx
import pytest

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _chat_sse_deltas,
    _final_speech,
    _progressive_text,
    _run_assist,
    _speech_agent,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _raw_client
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_seeded_assist_speech_streams_remain_isolated(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Many fragmented real Assist streams retain exact progressive speech."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    rng = random.Random(stress_seed ^ 0x5EEEC4)
    count = 8 if stress_scale == 1 else 24
    expected: list[str] = []
    replies: list[bytes] = []
    for index in range(count):
        word = f"Voice{index}_{rng.randrange(100000)}"
        expected.append(f"{word} see and done.")
        replies.append(
            _chat_sse_deltas(
                [
                    f"**{word}** see ht",
                    f"tps://example.com/{index} and ",
                    "done.",
                ]
            )
        )
    wire = _install_wire(monkeypatch, agent, replies)
    client = await hass_ws_client(hass)
    for index, spoken in enumerate(expected):
        events = await _run_assist(
            client,
            pipeline_id=agent.entity_id,
            conversation_id=f"nightly-speech-{stress_seed}-{index}",
        )
        assert _progressive_text(events) == spoken
        assert _final_speech(events) == spoken
        assert "http" not in spoken
        if index:
            assert expected[index - 1] not in _progressive_text(events)
    assert len(wire.requests) == count
    assert all(request["body"]["stream"] is True for request in wire.requests)
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        assist_speech_streams=count,
        provider_requests=len(wire.requests),
    )


class _GatedSpeechStream(httpx.AsyncByteStream):
    """Pause a real HTTPX SSE stream after its first provider fragment."""

    def __init__(self, first: bytes, rest: bytes) -> None:
        self.first = first
        self.rest = rest
        self.first_delivered = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        yield self.first
        self.first_delivered.set()
        await self.release.wait()
        yield self.rest


@pytest.mark.asyncio
async def test_cancelled_fragment_cannot_leak_into_next_assist_stream(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A cancelled provider stream leaves the next public Assist turn clean."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    raw = _chat_sse_deltas(["**Stale** ht", "tps://example.com/old done."])
    split = raw.index(b"\n\n") + 2
    stream = _GatedSpeechStream(raw[:split], raw[split:])
    requests: list[httpx.Request] = []

    async def send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    cancelled = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Begin a stream that will be cancelled",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )
    )
    try:
        await asyncio.wait_for(stream.first_delivered.wait(), timeout=10)
        cancelled.cancel()
    finally:
        stream.release.set()
        with suppress(asyncio.CancelledError):
            await cancelled
    assert cancelled.done()
    assert len(requests) == 1

    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_deltas(["**Fresh** see ht", "tps://example.com/new and done."])],
    )
    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="nightly-speech-after-cancellation",
    )
    assert _progressive_text(events) == "Fresh see and done."
    assert _final_speech(events) == "Fresh see and done."
    assert "Stale" not in str(events)
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        cancelled_speech_streams=1,
        recovered_speech_streams=1,
    )
