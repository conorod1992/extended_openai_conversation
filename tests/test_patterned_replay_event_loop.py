"""Patterned-key replay comparison must not block HA's event loop.

Retain the same key membership, coercion and receipt evidence used before the
comparison moved off-loop. Inability to prove equivalence must never dispatch a
possibly repeated effect.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
from threading import Event, get_ident
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    regex_execution,
    tool_replay_guard,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    MalformedToolArguments,
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    append_unresolved_tool_results,
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    _signature,
    async_was_unacknowledged_equivalent,
    bind_effect_replay_scope,
    record_dispatch,
    remember_unacknowledged_calls,
    replay_safety,
    was_unacknowledged_equivalent,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)

def _patterned_tool():
    return _tool(
        "effect",
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "patternProperties": {"^extra_": {}},
            "additionalProperties": False,
        },
    )


async def _first_effect(hass, monkeypatch):
    """Record one real validated tool exchange and its lost acknowledgment."""
    entity, log = _entity(hass), _chat_log(hass)
    definition = _patterned_tool()
    original = _call("original", "effect", {"count": 3, "extra_note": "yes"})
    effects = []

    async def execute(tool, validated, *_args):
        record_dispatch(
            entity, validated, tool, prepared_arguments=validated.tool_args
        )
        effects.append(deepcopy(validated.tool_args))
        return _result(entity, validated)

    entity._execute_function_tool.side_effect = execute

    async def executor_job(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", executor_job)
    before = {id(item) for item in log.content}
    _retain_calls(log, [original])
    await async_execute_tool_exchange(
        entity,
        log,
        [original],
        [definition],
        FunctionCallBudget(5),
        None,
        [],
        recovery_state=ToolRecoveryState(enabled=True),
    )
    remember_unacknowledged_calls(entity, log, before)
    assert effects == [{"count": 3, "extra_note": "yes"}]
    return entity, log, definition, effects


async def test_patterned_replay_equivalence_preserves_normalised_inputs_off_loop(
    hass, monkeypatch
) -> None:
    loop_thread = get_ident()
    worker_threads = []

    def worker(payload):
        worker_threads.append(get_ident())
        return {
            "results": [
                any(re.search(pattern, key) for pattern in payload["patterns"])
                for key in payload["keys"]
            ],
            "invalid": [],
        }

    monkeypatch.setattr(regex_execution, "_run_regex_worker", worker)
    entity, log, tool, effects = await _first_effect(hass, monkeypatch)
    assert await async_was_unacknowledged_equivalent(
        entity, log, _call("new-id", "effect", {"count": "3", "extra_note": "yes"})
    )
    assert not await async_was_unacknowledged_equivalent(
        entity, log, _call("different", "effect", {"count": "4", "extra_note": "yes"})
    )
    assert not await async_was_unacknowledged_equivalent(
        entity, log, _call("different-tool", "another", {"count": "3"})
    )
    assert worker_threads and all(thread != loop_thread for thread in worker_threads)

    retry = _call("retry", "effect", {"count": "3", "extra_note": "yes"})
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity,
            log,
            [retry],
            [tool],
            FunctionCallBudget(5),
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )
    assert effects == [{"count": 3, "extra_note": "yes"}]


async def test_patterned_replay_worker_failure_blocks_dispatch(hass, monkeypatch) -> None:
    entity, log, tool, effects = await _first_effect(hass, monkeypatch)
    monkeypatch.setattr(
        regex_execution,
        "_run_regex_worker",
        lambda _payload: {"results": [], "invalid": []},
    )
    retry = _call("retry", "effect", {"count": "3", "extra_note": "yes"})
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="Unable to verify.*replay safety"):
        await async_execute_tool_exchange(
            entity,
            log,
            [retry],
            [tool],
            FunctionCallBudget(5),
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )
    assert effects == [{"count": 3, "extra_note": "yes"}]


def test_unprepared_patterned_dispatch_records_uncertain_provenance_without_regex(
    hass, monkeypatch
) -> None:
    entity, log = _entity(hass), _chat_log(hass)
    original = _call("original", "effect", {"count": "3", "extra_note": "yes"})
    definition = _patterned_tool()
    monkeypatch.setattr(
        regex_execution,
        "_run_regex_worker",
        lambda _payload: pytest.fail("Dispatch recording must not run regex"),
    )
    before = {id(item) for item in log.content}
    _retain_calls(log, [original])
    with bind_effect_replay_scope(entity, log.conversation_id):
        record_dispatch(entity, original, definition)

    signature, spec = entity._dispatched_tool_signatures[id(original)]
    assert signature == _signature(original)
    assert spec["_effective_arguments_unknown"] is True
    entity._execution_tool_outcomes = {id(original): "completed"}
    remember_unacknowledged_calls(entity, log, before)
    assert "effect" in replay_safety(entity).uncertain_tools[log.conversation_id]
    assert was_unacknowledged_equivalent(
        entity, log, _call("different", "effect", {"count": 4})
    )


async def test_unrelated_tool_skips_patterned_replay_worker(hass, monkeypatch) -> None:
    entity, log, _tool_def, _effects = await _first_effect(hass, monkeypatch)
    monkeypatch.setattr(
        regex_execution,
        "_run_regex_worker",
        lambda _payload: pytest.fail("Unrelated tool cannot need regex classification"),
    )
    assert not await async_was_unacknowledged_equivalent(
        entity, log, _call("new", "different-tool", {"count": "3"})
    )


def _untyped_call(call_id: str, arguments: Any) -> llm.ToolInput:
    """Retain valid JSON with an invalid top-level argument type unchanged."""
    return llm.ToolInput(
        id=call_id, tool_name="effect", tool_args=arguments, external=True
    )


def _results_for(
    log: conversation.ChatLog, call_id: str
) -> list[conversation.ToolResultContent]:
    return [
        item
        for item in log.content
        if isinstance(item, conversation.ToolResultContent)
        and item.tool_call_id == call_id
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        7,
        True,
        "not an object",
        ["not an object"],
        [["count", "3"], ["extra_note", "yes"]],
    ],
)
async def test_patterned_replay_preserves_invalid_argument_shape_for_recovery(
    hass, monkeypatch, arguments: Any
) -> None:
    """Replay must not coerce arrays or raise before argument recovery."""
    entity, log, definition, effects = await _first_effect(hass, monkeypatch)
    retry = _untyped_call("retry-invalid", arguments)
    _retain_calls(log, [retry])
    budget = FunctionCallBudget(3)
    recovery = ToolRecoveryState(enabled=True)

    await async_execute_tool_exchange(
        entity,
        log,
        [retry],
        [definition],
        budget,
        None,
        [],
        recovery_state=recovery,
    )

    assert effects == [{"count": 3, "extra_note": "yes"}]
    assert budget.used == 0
    assert recovery.used == 1
    results = _results_for(log, retry.id)
    assert len(results) == 1
    result = tool_result_data(results[0])["result"]
    assert result["reason"] == "correctable_tool_error"
    assert result["code"] == "invalid_arguments"
    assert result["stage"] == "pre_dispatch_validation"


@pytest.mark.parametrize(
    "arguments",
    [
        None,  # Previously raised TypeError before reaching the validator.
        # Previously became an equivalent replay after dict() coercion.
        [["count", "3"], ["extra_note", "yes"]],
    ],
)
async def test_patterned_replay_invalid_arguments_keep_fail_fast_budget(
    hass, monkeypatch, arguments: Any
) -> None:
    """Without recovery, invalid shapes reach validation, not replay rejection."""
    entity, log, definition, effects = await _first_effect(hass, monkeypatch)
    retry = _untyped_call("retry-invalid", arguments)
    _retain_calls(log, [retry])
    budget = FunctionCallBudget(3)
    attempts: list[Any] = []

    async def existing_validator(_definition, tool_input, *_args):
        attempts.append(tool_input.tool_args)
        validate_function_arguments(definition["spec"], tool_input.tool_args)
        pytest.fail("Invalid input must not dispatch a side effect")

    entity._execute_function_tool.side_effect = existing_validator
    with pytest.raises(HomeAssistantError, match="Function input must be an object"):
        await async_execute_tool_exchange(
            entity,
            log,
            [retry],
            [definition],
            budget,
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=False),
        )

    assert attempts == [arguments]
    assert budget.used == 1  # Legacy non-recovery boundary claims before validation.
    assert effects == [{"count": 3, "extra_note": "yes"}]
    assert len(_results_for(log, retry.id)) == 1


async def test_patterned_replay_preserves_malformed_json_recovery_marker(
    hass, monkeypatch
) -> None:
    """Copying the candidate must not erase a malformed JSON marker's raw text."""
    entity, log, definition, effects = await _first_effect(hass, monkeypatch)
    recovery = ToolRecoveryState(enabled=True)
    marker = recovery.remember_malformed("retry-malformed", "{broken")
    retry = _untyped_call("retry-malformed", marker)
    _retain_calls(log, [retry])
    observed: list[Any] = []
    previous = tool_replay_guard._patterned_replay_signature

    def inspect_candidate(candidate, spec):
        observed.append(candidate.tool_args)
        return previous(candidate, spec)

    monkeypatch.setattr(
        tool_replay_guard, "_patterned_replay_signature", inspect_candidate
    )
    await async_execute_tool_exchange(
        entity,
        log,
        [retry],
        [definition],
        FunctionCallBudget(3),
        None,
        [],
        recovery_state=recovery,
    )
    assert len(observed) == 1
    assert isinstance(observed[0], MalformedToolArguments)
    assert observed[0].raw == "{broken"
    assert observed[0] is not marker
    assert effects == [{"count": 3, "extra_note": "yes"}]
    result = tool_result_data(_results_for(log, retry.id)[0])["result"]
    assert result["code"] == "invalid_json"


async def test_cancelling_patterned_replay_settles_all_calls_without_dispatch(
    hass, monkeypatch
) -> None:
    """Even a still-running worker cannot leave unpaired calls or execute later."""
    entity, log, definition, effects = await _first_effect(hass, monkeypatch)
    original_evidence = set(replay_safety(entity).schemas[log.conversation_id])
    before = {id(item) for item in log.content}
    pending_calls = [
        _call("pending-1", "effect", {"count": "4", "extra_note": "yes"}),
        _call("pending-2", "effect", {"count": "5", "extra_note": "yes"}),
    ]
    _retain_calls(log, pending_calls)
    entered, release, completed = Event(), Event(), Event()
    original_worker = tool_replay_guard._patterned_replay_signature

    def held_worker(candidate, spec):
        entered.set()
        try:
            if not release.wait(5):
                raise AssertionError("Replay worker was not released")
            return original_worker(candidate, spec)
        finally:
            completed.set()

    monkeypatch.setattr(tool_replay_guard, "_patterned_replay_signature", held_worker)
    budget = FunctionCallBudget(3)
    run = asyncio.create_task(
        async_execute_tool_exchange(
            entity,
            log,
            pending_calls,
            [definition],
            budget,
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 7)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert budget.used == 0
        assert effects == [{"count": 3, "extra_note": "yes"}]
        assert [len(_results_for(log, call.id)) for call in pending_calls] == [1, 1]
        statuses = [
            tool_result_data(_results_for(log, call.id)[0])["result"]["status"]
            for call in pending_calls
        ]
        assert statuses == ["error", "skipped"]

        # An outer cancellation handler can attempt cleanup again.
        snapshot = list(log.content)
        append_unresolved_tool_results(
            log, entity.entity_id, pending_calls, error=asyncio.CancelledError()
        )
        assert log.content == snapshot
        remember_unacknowledged_calls(entity, log, before)
        assert (
            set(replay_safety(entity).schemas[log.conversation_id])
            == original_evidence
        )

        # A later turn must be serializable in both supported provider APIs.
        log.async_add_user_content(conversation.UserContent(content="Follow up"))
        chat_items = _convert_content_to_param(log.content)
        responses_items = _convert_content_to_responses_param(log.content)
        for call in pending_calls:
            assert sum(
                item.get("role") == "tool" and item.get("tool_call_id") == call.id
                for item in chat_items
            ) == 1
            assert sum(
                item.get("type") == "function_call_output"
                and item.get("call_id") == call.id
                for item in responses_items
            ) == 1

        snapshot = list(log.content)
    finally:
        release.set()
        if not run.done():
            run.cancel()
            try:
                await run
            except asyncio.CancelledError:
                pass
    assert await asyncio.wait_for(asyncio.to_thread(completed.wait, 5), 7)
    assert log.content == snapshot
    assert budget.used == 0
    assert effects == [{"count": 3, "extra_note": "yes"}]
