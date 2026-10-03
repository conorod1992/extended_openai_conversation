"""Deliberate Script stops retain HA semantics; aborts do not claim success."""

import pytest

from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, trace


@pytest.mark.parametrize(
    "kind",
    [
        "normal",
        "stop",
        "condition",
        "continue_timeout",
        "abort_timeout",
        "error_stop",
        "missing_stop_response",
    ],
)
async def test_script_function_observes_native_outcome_and_preserves_parent_trace(
    hass, kind
):
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("script_probe", "record", record)
    action = {
        "normal": {"variables": {"_function_result": False}},
        "stop": {"stop": "intentional stop"},
        "condition": {"condition": "template", "value_template": "{{ false }}"},
        "continue_timeout": {
            "wait_template": "{{ false }}",
            "timeout": {"milliseconds": 10},
            "continue_on_timeout": True,
        },
        "abort_timeout": {
            "wait_template": "{{ false }}",
            "timeout": {"milliseconds": 10},
            "continue_on_timeout": False,
        },
        "error_stop": {"stop": "controlled abort", "error": True},
        "missing_stop_response": {
            "stop": "missing response",
            "response_variable": "missing",
        },
    }[kind]
    sequence = cv.SCRIPT_SCHEMA(
        [
            {"action": "script_probe.record", "data": {"marker": "before"}},
            action,
            {"action": "script_probe.record", "data": {"marker": "after"}},
        ]
    )
    parent = trace.StopReason()
    parent.script_execution = "parent unchanged"
    token = trace.script_execution_cv.set(parent)
    parent_trace = {}
    trace_token = trace.trace_cv.set(parent_trace)
    try:
        if kind in {"abort_timeout", "error_stop", "missing_stop_response"}:
            with pytest.raises(HomeAssistantError, match="aborted before completion"):
                await ScriptFunction().execute(
                    hass, {"sequence": sequence}, {}, None, []
                )
        else:
            result = await ScriptFunction().execute(
                hass, {"sequence": sequence}, {}, None, []
            )
            assert result is False if kind == "normal" else result == "Success"
        assert trace.script_execution_cv.get() is parent
        assert parent.script_execution == "parent unchanged"
        assert trace.trace_cv.get() is parent_trace
        assert parent_trace == {}
    finally:
        trace.script_execution_cv.reset(token)
        trace.trace_cv.reset(trace_token)
    assert effects == (
        ["before", "after"] if kind in {"normal", "continue_timeout"} else ["before"]
    )
