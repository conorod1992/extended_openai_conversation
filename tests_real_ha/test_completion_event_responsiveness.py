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
    assert finished[0].data == {
        "schema_version": 2,
        "conversation_id": None,
        "agent_id": "event-agent",
        "status": "success",
    }
    assert max(gaps) < 0.25, (
        f"Retained result conversion stalled HA for {max(gaps):.3f}s"
    )


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("cancel", [False, True])
async def test_bus_failure_does_not_hide_later_native_completion_events(
    hass, monkeypatch, caplog, failed_index, cancel
):
    """One failed notification preserves later real HA events and request outcome."""
    from custom_components.extended_openai_conversation_responses import (
        completion_events,
    )

    entered = asyncio.Event()
    release = asyncio.Event()
    original = completion_events._async_emit
    secret = "private-event-content-do-not-log"

    async def emit(hass, payload):
        if payload["conversation_id"] == str(failed_index):
            entered.set()
            if cancel:
                await release.wait()
            completion_events._emit(
                SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *_args: fail())),
                payload,
            )
            return
        await original(hass, payload)

    def fail():
        raise ValueError(secret)

    monkeypatch.setattr(completion_events, "_async_emit", emit)
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
                SimpleNamespace(conversation_id=str(index), content=[]),
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
        assert [int(event.data["conversation_id"]) for event in finished] == [
            index for index in range(3) if index != failed_index
        ]
        assert "Completion notification failed" in caplog.text
        assert secret not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "archive_enabled,private", [(False, False), (True, False), (True, True)]
)
async def test_completion_event_recording_contains_metadata_only(
    hass, monkeypatch, archive_enabled, private
):
    """Exercise public Assist and the actual Recorder event serialization boundary."""
    from homeassistant.components import conversation
    from homeassistant.components.recorder.db_schema import EventData
    from homeassistant.core import Context
    from pytest_homeassistant_custom_component.common import MockUser
    from tests_real_ha.test_cross_feature_acceptance import _agent, _provider

    agent = await _agent(hass, archive_enabled=archive_enabled)
    secret = "PRIVATE_COMPLETION_INPUT_VALUE"
    system_secret = "PRIVATE_COMPLETION_SYSTEM_VALUE"
    events = []
    hass.bus.async_listen(EVENT_CONVERSATION_FINISHED, events.append)
    user = MockUser(id="completion-owner").add_to_hass(hass)
    replies = []
    if private:
        replies.append(
            {
                "index": 0,
                "id": "private-call",
                "type": "function",
                "function": {"name": "conversation_private", "arguments": "{}"},
            }
        )
    replies.append("PRIVATE_COMPLETION_ANSWER_VALUE")
    _provider(monkeypatch, agent, replies)
    result = await conversation.async_converse(
        hass=hass,
        text=secret,
        conversation_id=None,
        context=Context(user_id=user.id),
        language="en",
        agent_id=agent.entry.entry_id,
        extra_system_prompt=system_secret,
    )
    assert result.response.error_code is None
    await hass.async_block_till_done()
    assert len(events) == 1
    encoded = EventData.shared_data_bytes_from_event(events[0], "sqlite")
    assert encoded is not None
    for value in (secret, system_secret, "PRIVATE_COMPLETION_ANSWER_VALUE"):
        assert value.encode() not in encoded
    assert set(events[0].data) == {
        "schema_version",
        "agent_id",
        "conversation_id",
        "status",
    }
    assert events[0].data["status"] == "success"
