"""Real HA cancellation coverage after a completed service side effect."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _agent,
    _prepare_service,
    _raw_client,
    _say,
    _speech,
    _tool_result_from_chat_request,
)


async def test_cancel_after_service_side_effect_does_not_replay_action(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Cancellation after execution must leave the runtime healthy without replay."""
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    calls = await _prepare_service(hass)
    continuation_started = asyncio.Event()
    block_continuation = asyncio.Event()
    requests: list[dict[str, Any]] = []

    async def send(
        request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        body = json.loads(request.content.decode())
        requests.append({"path": request.url.path, "body": body})
        index = len(requests) - 1

        if index == 0:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_chat_sse_tool_call(),
                request=request,
            )

        if index == 1:
            # Reaching the provider continuation proves the genuine HA service call
            # has already completed and its tool result has been serialized.
            tool_result = _tool_result_from_chat_request(body)
            assert tool_result["result"][0]["success"] is True
            assert len(calls) == 1
            continuation_started.set()
            await block_continuation.wait()
            raise AssertionError("Cancelled provider continuation unexpectedly resumed")

        if index == 2:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_chat_sse_text("Recovered after cancellation."),
                request=request,
            )

        raise AssertionError("Unexpected extra OpenAI SDK request")

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)

    first_turn = asyncio.create_task(_say(hass, agent))
    await asyncio.wait_for(continuation_started.wait(), timeout=5)

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.provider_wire"]

    first_turn.cancel()
    try:
        await first_turn
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Conversation cancellation did not propagate")

    # Cancellation must not cause the already-completed action to be retried, and
    # the same loaded agent must remain usable for a fresh independent request.
    assert len(calls) == 1
    recovered = await _say(hass, agent)

    assert _speech(recovered) == "Recovered after cancellation."
    assert len(calls) == 1
    assert [request["path"] for request in requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]
