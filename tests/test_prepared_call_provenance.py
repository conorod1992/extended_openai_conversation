"""Prepared validation and retained-call provenance share one execution lifetime."""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_execution as execution,
    tool_replay_guard as replay,
)
from homeassistant.helpers import llm


@contextmanager
def prepared_call(call, retained, spec):
    """Exercise the single prepared-dispatch owner."""
    with execution.validated_function_call(call, spec, retained_tool_input=retained):
        yield


def calls(value):
    # Identical external IDs must not establish ownership without object identity.
    return (
        llm.ToolInput(id="shared-id", tool_name="effect", tool_args={"value": value}),
        llm.ToolInput(id="shared-id", tool_name="effect", tool_args={"value": value}),
    )


def assert_origin(call, retained=None):
    inputs = replay.dispatch_inputs(call)
    assert inputs[0] is call
    if retained is None:
        assert len(inputs) == 1
    else:
        assert len(inputs) == 2
        assert inputs[1] is retained


async def test_nested_prepared_calls_restore_parent_provenance(hass, monkeypatch):
    parent, parent_retained = calls(1)
    child, child_retained = calls(2)
    spec = {"parameters": {"type": "object"}}
    validate = AsyncMock(return_value={"revalidated": True})
    monkeypatch.setattr(execution, "async_validate_function_arguments", validate)

    with prepared_call(parent, parent_retained, spec):
        assert_origin(parent, parent_retained)
        assert_origin(child)
        assert (
            await execution.async_execution_arguments(hass, spec, parent)
            is parent.tool_args
        )
        with prepared_call(child, child_retained, spec):
            assert_origin(child, child_retained)
            assert_origin(parent)
            assert (
                await execution.async_execution_arguments(hass, spec, child)
                is child.tool_args
            )
            assert await execution.async_execution_arguments(hass, spec, parent) == {
                "revalidated": True
            }
        assert_origin(parent, parent_retained)
        assert_origin(child)
        assert (
            await execution.async_execution_arguments(hass, spec, parent)
            is parent.tool_args
        )

    assert validate.await_count == 1
    validate.assert_awaited_once_with(hass, spec, parent.tool_args)
    assert_origin(parent)
    assert_origin(child)
    assert execution._VALIDATED_CALL.get() is None


async def test_concurrent_prepared_calls_keep_distinct_retained_aliases(
    hass, monkeypatch
):
    first, first_retained = calls(1)
    second, second_retained = calls(2)
    spec = {"parameters": {"type": "object"}}
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    validate = AsyncMock()
    monkeypatch.setattr(execution, "async_validate_function_arguments", validate)

    async def run(index, call, retained, other):
        with prepared_call(call, retained, spec):
            ready[index].set()
            await release.wait()
            assert_origin(call, retained)
            assert_origin(other)
            assert (
                await execution.async_execution_arguments(hass, spec, call)
                is call.tool_args
            )
        assert_origin(call)
        assert execution._VALIDATED_CALL.get() is None

    tasks = [
        asyncio.create_task(run(0, first, first_retained, second)),
        asyncio.create_task(run(1, second, second_retained, first)),
    ]
    try:
        await asyncio.gather(*(event.wait() for event in ready))
        assert execution._VALIDATED_CALL.get() is None
        assert_origin(first)
        assert_origin(second)
        release.set()
        await asyncio.gather(*tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    validate.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["schema", "arguments"])
async def test_mutation_requires_revalidation_without_losing_retained_origin(
    hass, monkeypatch, mutation
):
    call, retained = calls(1)
    spec = {"parameters": {"type": "object"}}
    revalidated = {"revalidated": True}
    validate = AsyncMock(return_value=revalidated)
    monkeypatch.setattr(execution, "async_validate_function_arguments", validate)

    with prepared_call(call, retained, spec):
        assert (
            await execution.async_execution_arguments(hass, spec, call)
            is call.tool_args
        )
        validate.assert_not_awaited()
        if mutation == "schema":
            spec["description"] = "Changed after preparation"
        else:
            call.tool_args["value"] = 2
        assert (
            await execution.async_execution_arguments(hass, spec, call) is revalidated
        )
        validate.assert_awaited_once_with(hass, spec, call.tool_args)
        assert_origin(call, retained)
        assert retained.tool_args == {"value": 1}

    assert_origin(call)
    assert execution._VALIDATED_CALL.get() is None


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_failure_restores_both_prepared_call_bindings(failure):
    call, retained = calls(1)
    with pytest.raises(failure), prepared_call(call, retained, {}):
        assert_origin(call, retained)
        raise failure("interrupted prepared call")
    assert_origin(call)
    assert execution._VALIDATED_CALL.get() is None


async def test_child_cancellation_restores_inherited_parent_without_corrupting_it(
    hass, monkeypatch
):
    parent, parent_retained = calls(1)
    child, child_retained = calls(2)
    spec = {"parameters": {"type": "object"}}
    ready = asyncio.Event()
    never = asyncio.Event()
    validate = AsyncMock()
    monkeypatch.setattr(execution, "async_validate_function_arguments", validate)

    async def run_child():
        try:
            with prepared_call(child, child_retained, spec):
                ready.set()
                await never.wait()
        except asyncio.CancelledError:
            assert_origin(parent, parent_retained)
            assert_origin(child)
            assert (
                await execution.async_execution_arguments(hass, spec, parent)
                is parent.tool_args
            )
            raise

    with prepared_call(parent, parent_retained, spec):
        task = asyncio.create_task(run_child())
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_origin(parent, parent_retained)
        assert_origin(child)
        assert (
            await execution.async_execution_arguments(hass, spec, parent)
            is parent.tool_args
        )

    validate.assert_not_awaited()
    assert_origin(parent)
    assert execution._VALIDATED_CALL.get() is None


async def test_parent_cancellation_does_not_clear_independent_child_preparation(
    hass, monkeypatch
):
    parent, parent_retained = calls(1)
    child, child_retained = calls(2)
    spec = {"parameters": {"type": "object"}}
    ready = asyncio.Event()
    release = asyncio.Event()
    never = asyncio.Event()
    child_tasks = []
    completed = []
    validate = AsyncMock()
    monkeypatch.setattr(execution, "async_validate_function_arguments", validate)

    async def run_child():
        with prepared_call(child, child_retained, spec):
            ready.set()
            await release.wait()
            assert_origin(child, child_retained)
            assert_origin(parent)
            assert (
                await execution.async_execution_arguments(hass, spec, child)
                is child.tool_args
            )
            completed.append("child")

    async def run_parent():
        with prepared_call(parent, parent_retained, spec):
            child_tasks.append(asyncio.create_task(run_child()))
            await never.wait()

    task = asyncio.create_task(run_parent())
    try:
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not child_tasks[0].done()
        assert_origin(parent)
        assert execution._VALIDATED_CALL.get() is None
        release.set()
        await child_tasks[0]
    finally:
        task.cancel()
        release.set()
        await asyncio.gather(task, *child_tasks, return_exceptions=True)
    assert completed == ["child"]
    validate.assert_not_awaited()
