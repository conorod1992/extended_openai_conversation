"""Patterned-key replay comparison must not block HA's event loop.

Retain the same key membership, coercion and receipt evidence used before the
comparison moved off-loop. Inability to prove equivalence must never dispatch a
possibly repeated effect.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
from threading import get_ident
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import regex_execution
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
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
from homeassistant.exceptions import HomeAssistantError
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
