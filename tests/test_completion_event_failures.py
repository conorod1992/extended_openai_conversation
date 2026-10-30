"""A failed completion notification must not replace completed request outcomes."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import completion_events
from custom_components.extended_openai_conversation_responses.completion_events import (
    CompletionMetadata,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)

_SECRET = "private-completion-result-do-not-log"


class Content:
    def __init__(self, index, *, fail=False):
        self.index = index
        self.fail = fail

    def as_dict(self):
        if self.fail:
            raise ValueError(_SECRET)
        return {"index": self.index}


def _assert_warning(caplog):
    records = [r for r in caplog.records if r.name == completion_events.__name__]
    assert len([r for r in records if r.levelno >= logging.WARNING]) == 1
    assert "Completion notification failed" in caplog.text
    assert "ValueError" in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_bus_failure_retains_request_outcome_and_later_events(
    failed_index, direct, outcome, caplog
):
    caplog.set_level(logging.DEBUG, logger=completion_events.__name__)

    def fire(_event, payload):
        if int(payload["conversation_id"]) == failed_index:
            raise ValueError(_SECRET)

    fire_mock = Mock(side_effect=fire)
    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fire_mock))
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass
    response = object()

    async def process(_request):
        for index in range(3):
            completion_events.queue_completion_event(
                hass,
                CompletionMetadata(
                    agent_id="agent", conversation_id=str(index), status="success"
                ),
                [Content(index, fail=index == failed_index)],
            )
        if outcome == "error":
            raise RuntimeError("original request failure")
        if outcome == "cancel":
            raise asyncio.CancelledError("original cancellation")
        return response

    agent._async_process = process
    call = (
        agent.async_process_direct(object())
        if direct
        else agent.async_process(object())
    )
    if outcome == "success":
        result = await call
        assert (result[0] if direct else result) is response
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="original request failure"):
            await call
    else:
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await call
    assert [
        int(call.args[1]["conversation_id"])
        for call in hass.bus.async_fire.call_args_list
    ] == [index for index in range(3)]
    _assert_warning(caplog)


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("cancels", [1, 2])
async def test_real_cancellation_during_failed_delivery_still_drains_queue(
    failed_index, cancels, caplog, monkeypatch
):
    entered = asyncio.Event()
    release = asyncio.Event()
    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    original = completion_events._async_emit

    async def blocked_emit(hass, payload):
        if int(payload["conversation_id"]) == failed_index:
            entered.set()
            await release.wait()
            # Exercise the same best-effort bus boundary after suspension.
            completion_events._emit(
                SimpleNamespace(
                    bus=SimpleNamespace(
                        async_fire=Mock(side_effect=ValueError(_SECRET))
                    )
                ),
                payload,
            )
            return
        await original(hass, payload)

    monkeypatch.setattr(completion_events, "_async_emit", blocked_emit)

    async def process():
        async with completion_events.completion_event_scope():
            for index in range(3):
                completion_events.queue_completion_event(
                    hass,
                    CompletionMetadata(
                        agent_id="agent", conversation_id=str(index), status="success"
                    ),
                    [Content(index, fail=True)],
                )

    task = asyncio.create_task(process())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(cancels):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [
            int(call.args[1]["conversation_id"])
            for call in hass.bus.async_fire.call_args_list
        ] == [index for index in range(3) if index != failed_index]
        _assert_warning(caplog)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("standalone", [False, True])
async def test_bus_failure_isolated_from_other_events(standalone, caplog):
    delivered = []

    def fire(_event_type, payload):
        if int(payload["conversation_id"]) == 1:
            raise ValueError(_SECRET)
        delivered.append(int(payload["conversation_id"]))

    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fire))
    if standalone:
        for index in range(3):
            completion_events.queue_completion_event(
                hass,
                CompletionMetadata(
                    agent_id="agent", conversation_id=str(index), status="success"
                ),
                [Content(index)],
            )
    else:
        async with completion_events.completion_event_scope():
            for index in range(3):
                completion_events.queue_completion_event(
                    hass,
                    CompletionMetadata(
                        agent_id="agent", conversation_id=str(index), status="success"
                    ),
                    [Content(index)],
                )
    assert delivered == [0, 2]
    _assert_warning(caplog)


async def test_standalone_never_converts_private_history():
    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
    completion_events.queue_completion_event(
        hass,
        CompletionMetadata(agent_id="agent", conversation_id=None, status="success"),
        [Content(0, fail=True)],
    )
    hass.bus.async_fire.assert_called_once()
    assert hass.bus.async_fire.call_args.args[1] == {
        "schema_version": 2,
        "agent_id": "agent",
        "conversation_id": None,
        "status": "success",
    }
