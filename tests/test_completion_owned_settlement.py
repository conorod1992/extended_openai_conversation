"""Completion queue ownership survives intersecting request and delivery outcomes."""

import asyncio
from contextvars import ContextVar
import logging
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import completion_events
from custom_components.extended_openai_conversation_responses.completion_events import (
    CompletionMetadata,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)

_PRIVATE_BUS_FAILURE = "PRIVATE_COMPLETION_NOTIFICATION_FAILURE"


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("cancels", [0, 3])
async def test_failed_delivery_drains_before_exact_request_outcome(
    direct, outcome, cancels, monkeypatch, caplog
):
    """Late cancellation has its existing priority even when the body already failed."""
    entered, release = asyncio.Event(), asyncio.Event()
    attempted, delivered = [], []
    owner_context = ContextVar("completion_delivery_owner", default=None)
    marker = object()
    result = object()
    original_failure = RuntimeError("original request failure")
    original_cancellation = asyncio.CancelledError("original request cancellation")

    def fire(_event, payload):
        assert owner_context.get() is marker
        attempted.append(int(payload["conversation_id"]))
        if int(payload["conversation_id"]) == 1:
            raise ValueError(_PRIVATE_BUS_FAILURE)
        delivered.append(int(payload["conversation_id"]))

    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fire))
    emit = completion_events._async_emit

    async def held_emit(hass, payload):
        if int(payload["conversation_id"]) == 1:
            entered.set()
            await release.wait()
        await emit(hass, payload)

    monkeypatch.setattr(completion_events, "_async_emit", held_emit)
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = hass

    async def process(_request):
        for index in range(3):
            completion_events.queue_completion_event(
                hass,
                CompletionMetadata(
                    agent_id="agent", conversation_id=str(index), status="success"
                ),
            )
        if outcome == "failure":
            raise original_failure
        if outcome == "cancelled":
            raise original_cancellation
        return result

    agent._async_process = process

    async def invoke():
        token = owner_context.set(marker)
        try:
            if direct:
                response, _metadata = await agent.async_process_direct(object())
                return response
            return await agent.async_process(object())
        finally:
            owner_context.reset(token)

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert attempted == [0]
        for index in range(cancels):
            task.cancel(f"cancel during delivery {index}")
            await asyncio.sleep(0)
            assert not task.done()
            assert attempted == [0]
        release.set()
        if cancels:
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("cancel during delivery 0",)
        elif outcome == "failure":
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is original_failure
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value is original_cancellation
        else:
            assert await task is result
        assert attempted == [0, 1, 2]
        assert delivered == [0, 2]
        records = [
            record
            for record in caplog.records
            if record.name == completion_events.__name__
            and record.levelno >= logging.WARNING
        ]
        assert len(records) == 1
        assert "Completion notification failed" in records[0].getMessage()
        assert "ValueError" in records[0].getMessage()
        assert _PRIVATE_BUS_FAILURE not in caplog.text
        assert owner_context.get() is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancels", [0, 3])
async def test_nested_scope_does_not_lose_outer_queue_when_inner_delivery_fails(
    cancels, monkeypatch, caplog
):
    """An interrupted inner flush leaves its enclosing invocation's queue owned."""
    entered, release = asyncio.Event(), asyncio.Event()
    attempted, delivered = [], []

    def fire(_event, payload):
        label = payload["conversation_id"]
        attempted.append(label)
        if label == "inner-failure":
            raise ValueError(_PRIVATE_BUS_FAILURE)
        delivered.append(label)

    hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fire))
    emit = completion_events._async_emit

    async def held_emit(hass, payload):
        if payload["conversation_id"] == "inner-first":
            entered.set()
            await release.wait()
        await emit(hass, payload)

    monkeypatch.setattr(completion_events, "_async_emit", held_emit)

    async def invoke():
        async with completion_events.completion_event_scope():
            completion_events.queue_completion_event(
                hass,
                CompletionMetadata(
                    agent_id="agent", conversation_id="outer-first", status="success"
                ),
            )
            try:
                async with completion_events.completion_event_scope():
                    for label in ["inner-first", "inner-failure", "inner-last"]:
                        completion_events.queue_completion_event(
                            hass,
                            CompletionMetadata(
                                agent_id="agent",
                                conversation_id=label,
                                status="success",
                            ),
                        )
            finally:
                completion_events.queue_completion_event(
                    hass,
                    CompletionMetadata(
                        agent_id="agent", conversation_id="outer-last", status="success"
                    ),
                )

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert attempted == []
        for index in range(cancels):
            task.cancel(f"inner cancellation {index}")
            await asyncio.sleep(0)
            assert not task.done()
            assert attempted == []
        release.set()
        if cancels:
            with pytest.raises(asyncio.CancelledError, match="inner cancellation 0"):
                await task
        else:
            await task
        assert attempted == [
            "inner-first",
            "inner-failure",
            "inner-last",
            "outer-first",
            "outer-last",
        ]
        assert delivered == ["inner-first", "inner-last", "outer-first", "outer-last"]
        assert _PRIVATE_BUS_FAILURE not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancels", [1, 3])
async def test_blocked_cancelled_scope_does_not_delay_or_capture_another_scope(
    cancels, monkeypatch
):
    """Independent invocations retain their own order and completion latency."""
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []
    owner_context = ContextVar("independent_completion_owner", default=None)
    hass = SimpleNamespace(
        bus=SimpleNamespace(
            async_fire=lambda _event, payload: delivered.append(
                (
                    owner_context.get(),
                    payload["agent_id"],
                    int(payload["conversation_id"]),
                )
            )
        )
    )
    emit = completion_events._async_emit

    async def held_emit(hass, payload):
        if payload["agent_id"] == "blocked" and payload["conversation_id"] == "0":
            entered.set()
            await release.wait()
        await emit(hass, payload)

    monkeypatch.setattr(completion_events, "_async_emit", held_emit)

    async def invoke(owner):
        token = owner_context.set(owner)
        try:
            async with completion_events.completion_event_scope():
                for index in range(2):
                    completion_events.queue_completion_event(
                        hass,
                        CompletionMetadata(
                            agent_id=owner,
                            conversation_id=str(index),
                            status="success",
                        ),
                    )
            return owner
        finally:
            owner_context.reset(token)

    task = asyncio.create_task(invoke("blocked"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for index in range(cancels):
            task.cancel(f"blocked cancellation {index}")
            await asyncio.sleep(0)
        assert not task.done()
        assert await asyncio.wait_for(invoke("independent"), 1) == "independent"
        assert delivered == [
            ("independent", "independent", 0),
            ("independent", "independent", 1),
        ]
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="blocked cancellation 0"):
            await task
        assert delivered == [
            ("independent", "independent", 0),
            ("independent", "independent", 1),
            ("blocked", "blocked", 0),
            ("blocked", "blocked", 1),
        ]
        assert owner_context.get() is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
