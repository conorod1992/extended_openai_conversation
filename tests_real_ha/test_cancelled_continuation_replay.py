"""Confirm the overnight reports on current develop through genuine HA paths."""

import asyncio
import json

import httpx
import pytest

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.setup import async_setup_component
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _chat_sse_tool_call,
    _raw_client,
)


@pytest.mark.parametrize("repeat", [0])
@pytest.mark.parametrize("failure", ["cancel", "disconnect"])
async def test_completed_counter_effect_is_not_replayed_after_lost_continuation(
    hass,
    monkeypatch,
    failure,
    repeat,
):
    agent = await _agent(hass, "chat_completions")
    assert await async_setup_component(
        hass,
        "counter",
        {
            "counter": {"audit_overnight_effect": {"initial": 0, "step": 1}},
        },
    )
    await hass.async_block_till_done()
    entity_id = "counter.audit_overnight_effect"
    async_expose_entity(hass, "conversation", entity_id, True)
    assert hass.states.get(entity_id).state == "0"
    arguments = {
        "list": [
            {
                "domain": "counter",
                "service": "increment",
                "service_data": {"entity_id": [entity_id]},
            }
        ]
    }
    started = asyncio.Event()
    hold = asyncio.Event()
    requests = []

    async def send(request, *args, **kwargs):
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        if index in (0, 2):
            payload = _chat_sse_tool_call(
                call_id="original-call" if index == 0 else "retry-new-call",
                arguments=arguments,
            )
        elif index == 1:
            assert hass.states.get(entity_id).state == "1"
            assert any(message.get("role") == "tool" for message in body["messages"])
            started.set()
            if failure == "disconnect":
                raise httpx.ReadError("Synthetic lost continuation", request=request)
            await hold.wait()
            raise AssertionError("Cancelled continuation unexpectedly resumed")
        elif index == 3:
            payload = _chat_sse_text("Completed.")
        else:
            raise AssertionError("Unexpected SDK request")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    # Match existing replay witnesses: expose an exhausted ordinary disconnect
    # to EOAI rather than allowing an SDK transport retry to consume our next turn.
    monkeypatch.setattr(_raw_client(agent), "max_retries", 0)
    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    conversation_id = f"audit-overnight-replay-{repeat}"

    async def say():
        return await conversation.async_converse(
            hass=hass,
            text="Increment the audit counter",
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    task = asyncio.create_task(say())
    await asyncio.wait_for(started.wait(), timeout=5)
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.response.error_code is not None
    assert hass.states.get(entity_id).state == "1"
    await say()
    print(
        json.dumps(
            {
                "case": "counter_retry",
                "failure": failure,
                "repeat": repeat,
                "counter": hass.states.get(entity_id).state,
                "requests": len(requests),
                "remembered_effect": bool(
                    getattr(agent, "_unacknowledged_tool_calls", {})
                ),
            }
        )
    )
    assert hass.states.get(entity_id).state == "1", (
        "Retry repeated an already completed counter.increment after cancelled continuation"
    )
