"""Replay receipts: durable scheduling, batch uncertainty and composite data flow."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.execution_arguments import (
    execution_identity,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
    execution_completed,
    execution_prepared,
    execution_rejected,
    execution_started,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _retain_calls,
    _result,
)


def _effect_tool(function):
    return {
        "spec": {
            "name": "effect",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "service": {"type": "string"},
                    "entity_id": {"type": "string"},
                },
            },
        },
        "function": function,
    }


def _attempt_outcome(entity, call, tool, steps):
    with execution_attempt(entity, call, tool):
        execution_prepared(call.tool_args)
        for step in steps:
            if step == "start":
                execution_started()
            elif step == "complete":
                execution_completed()
            else:
                execution_rejected()
    return entity._execution_tool_outcomes[id(call)]


@pytest.mark.parametrize(
    "steps,expected",
    [
        (["start", "complete"], "completed"),
        (["start", "start", "complete"], "uncertain"),
        (["start", "reject"], "rejected"),
        (["start", "start", "reject"], "uncertain"),
        (["start", "complete", "start", "reject"], "partial"),
        (["start", "complete", "reject"], "partial"),
        (["reject"], "rejected"),
    ],
)
def test_attempt_never_discards_earlier_uncertainty(steps, expected):
    entity = SimpleNamespace()
    call = _call("first", "effect", {"domain": "light", "service": "toggle"})
    assert (
        _attempt_outcome(
            entity,
            call,
            _effect_tool({"type": "native", "name": "execute_service_single"}),
            steps,
        )
        == expected
    )


async def test_scheduled_commit_is_replay_protected_after_lost_acknowledgement(hass):
    entity = SimpleNamespace(entity_id="conversation.test")
    log = _chat_log(hass)
    call = _call("queue-1", "effect", {"domain": "light", "service": "toggle"})
    tool = _effect_tool({"type": "native", "name": "execute_service_single"})
    before = {id(item) for item in log.content}
    _retain_calls(log, [call])
    with execution_attempt(entity, call, tool):
        execution_prepared(call.tool_args)
        # The queue commit has the same side-effect receipt as a native action.
        execution_started()
        execution_completed()
    log.async_add_assistant_content_without_tools(
        _result(entity, call, {"result": "Scheduled"})
    )
    remember_unacknowledged_calls(entity, log, before)
    assert was_unacknowledged_equivalent(
        entity, log, _call("queue-2", "effect", call.tool_args)
    )


def test_composite_response_variable_preserves_first_child_effect_identity():
    native = {"type": "native", "name": "execute_service_single"}
    composite = {
        "type": "composite",
        "sequence": [{**native, "response_variable": "previous"}],
    }
    raw = {"domain": "light", "service": "toggle", "entity_id": "light.kitchen"}
    alternative = {
        "domain": "light",
        "service": "toggle",
        "service_data": {"entity_id": ["light.kitchen"]},
    }
    assert execution_identity(composite, raw) == execution_identity(
        composite, alternative
    )


def test_composite_later_child_retains_shared_input_after_response_assignment():
    config = {
        "type": "composite",
        "sequence": [
            {
                "type": "native",
                "name": "execute_service_single",
                "response_variable": "previous",
            },
            {"type": "template", "template": "{{ entity_id }}"},
        ],
    }
    first = {"domain": "light", "service": "toggle", "entity_id": "light.kitchen"}
    second = {
        "domain": "light",
        "service": "toggle",
        "service_data": {"entity_id": ["light.kitchen"]},
    }
    left = execution_identity(config, first)["composite"]
    right = execution_identity(config, second)["composite"]
    assert left[0] == right[0]
    assert left[1] != right[1]
