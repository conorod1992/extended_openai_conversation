"""Actual cancellation during conversion must drain the entire completion queue."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
    queue_completion_event,
)


@pytest.mark.parametrize("count,blocked_index", [(1, 0), (3, 0), (3, 1)])
@pytest.mark.parametrize("cancels", [1, 2])
async def test_task_cancel_during_worker_observes_all_deliveries(
    count, blocked_index, cancels
):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

    class Content:
        def __init__(self, index):
            self.index = index

        def as_dict(self):
            if self.index == blocked_index:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5), "test failed to release conversion worker"
            return {"index": self.index}

    async def request():
        async with completion_event_scope():
            for index in range(count):
                queue_completion_event(hass, {"index": index}, [Content(index)])

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
