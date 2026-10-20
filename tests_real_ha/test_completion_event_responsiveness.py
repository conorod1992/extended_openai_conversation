"""Real HA completion conversion remains responsive with accepted SQLite history."""

import asyncio
import logging
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
    # pytest enables bus DEBUG logging, which formats the entire event payload
    # synchronously. Measure normal HA logging, retaining the genuine bus call.
    bus_logger = logging.getLogger("homeassistant.core")
    prior_level = bus_logger.level
    bus_logger.setLevel(logging.INFO)
    # EventBus caches this level at construction and eagerly builds event repr
    # when enabled, even if the logger level is changed afterward.
    prior_bus_debug = hass.bus._debug
    hass.bus._debug = False
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
        bus_logger.setLevel(prior_level)
        hass.bus._debug = prior_bus_debug
    await hass.async_block_till_done()
    assert len(finished) == 1
    assert finished[0].data["messages"] == expected
    assert max(gaps) < 0.25, (
        f"Retained result conversion stalled HA for {max(gaps):.3f}s"
    )


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("cancel", [False, True])
async def test_conversion_failure_does_not_hide_later_native_completion_events(
    hass, monkeypatch, caplog, failed_index, cancel
):
    """One failed notification preserves later real HA events and request outcome."""
    import threading

    from homeassistant.components import conversation

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = conversation.UserContent.as_dict
    secret = "private-event-content-do-not-log"

    def as_dict(content):
        if content.content == "broken":
            loop.call_soon_threadsafe(entered.set)
            if cancel:
                assert release.wait(5), "test did not release conversion worker"
            raise ValueError(secret)
        return original(content)

    monkeypatch.setattr(conversation.UserContent, "as_dict", as_dict)
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.subentry = SimpleNamespace(subentry_id="completion-failure-agent")
    finished = []
    hass.bus.async_listen(
        EVENT_CONVERSATION_FINISHED, lambda event: finished.append(event)
    )

    async def process(_request):
        for index in range(3):
            agent._fire_conversation_finished(
                SimpleNamespace(index=index),
                SimpleNamespace(
                    content=[
                        conversation.UserContent(
                            content="broken" if index == failed_index else str(index)
                        )
                    ]
                ),
                status="success",
            )
        return "successful response"

    agent._async_process = process
    task = asyncio.create_task(agent.async_process(object()))
    try:
        if cancel:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == "successful response"
        await hass.async_block_till_done()
        assert [event.data["user_input"].index for event in finished] == [
            index for index in range(3) if index != failed_index
        ]
        assert "Completion notification failed" in caplog.text
        assert secret not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
