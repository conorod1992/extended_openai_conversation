"""Actual cancellation during delivery must drain the entire completion queue."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from custom_components.extended_openai_conversation_responses import completion_events
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
    queue_completion_event,
)


@pytest.mark.parametrize("count,blocked_index", [(1, 0), (3, 0), (3, 1)])
@pytest.mark.parametrize("cancels", [1, 2])
async def test_task_cancel_during_flush_observes_all_deliveries(
    count, blocked_index, cancels, monkeypatch
):
    entered = asyncio.Event()
    release = asyncio.Event()
    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    original = completion_events._async_emit

    async def blocked_emit(hass, payload):
        if payload["index"] == blocked_index:
            entered.set()
            await release.wait()
        await original(hass, payload)

    monkeypatch.setattr(completion_events, "_async_emit", blocked_emit)

    async def request():
        async with completion_event_scope():
            for index in range(count):
                queue_completion_event(hass, {"index": index})

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(cancels):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done(), (
            "request escaped before its completion queue was flushed"
        )
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [
            call.args[1]["index"] for call in hass.bus.async_fire.call_args_list
        ] == list(range(count))
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        # Also let pre-fix shielded tasks settle, avoiding leaked test workers.
        for _ in range(5):
            await asyncio.sleep(0)
