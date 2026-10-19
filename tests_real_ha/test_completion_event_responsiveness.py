"""Real HA completion conversion remains responsive with accepted SQLite history."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.const import (
    EVENT_CONVERSATION_FINISHED,
)
from tests.test_completion_event_workers import retained_sqlite_history


@pytest.mark.parametrize("rows", [10, 10000])
async def test_retained_backend_completion_does_not_block_ha(hass, tmp_path, rows):
    contents = await retained_sqlite_history(tmp_path, rows)
    expected = [item.as_dict() for item in contents]
    # Exercise the production invocation/event boundary with a genuine HA bus and
    # genuine HA message classes; no provider traffic is needed for conversion.
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.subentry = SimpleNamespace(subentry_id="event-agent")

    async def complete(request):
        agent._fire_conversation_finished(
            request, SimpleNamespace(content=contents), status="success"
        )
        return "Thanks"

    agent._async_process = complete
    finished = []
    hass.bus.async_listen(EVENT_CONVERSATION_FINISHED, finished.append)
    loop = asyncio.get_running_loop()
    prior_debug = loop.get_debug()
    loop.set_debug(False)
    running = True
    gaps = []

    async def heartbeat():
        previous = loop.time()
        while running:
            await asyncio.sleep(0.005)
            now = loop.time()
            gaps.append(now - previous)
            previous = now

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    try:
        assert await agent.async_process(SimpleNamespace()) == "Thanks"
        await asyncio.sleep(0.01)
    finally:
        running = False
        await pulse
        loop.set_debug(prior_debug)
    await hass.async_block_till_done()
    assert len(finished) == 1
    assert finished[0].data["messages"] == expected
    assert max(gaps) < 0.25, (
        f"Retained result conversion stalled HA for {max(gaps):.3f}s"
    )
