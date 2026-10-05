"""Deliberate Script stops retain HA semantics; aborts do not claim success."""

import asyncio
import os

import pytest

from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, trace
from homeassistant.helpers.script import Script, async_validate_actions_config
from tests.functions.behaviour_generators import assert_typed_value, script_cases


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
    if kind == "normal":

        async def response(_call):
            return {"count": 0, "flag": False, "empty": []}

        hass.services.async_register(
            "script_probe",
            "response",
            response,
            supports_response=SupportsResponse.ONLY,
        )
        for seed in {0x5C71, int(os.environ.get("STRESS_SEED", "12345"))}:
            for (
                raw_sequence,
                expected_effects,
                expected_result,
                _marker,
            ) in script_cases(seed):
                sequence = cv.SCRIPT_SCHEMA(raw_sequence)
                effects.clear()
                actual = await ScriptFunction().execute(
                    hass, {"sequence": sequence}, {}, None, []
                )
                assert effects == expected_effects
                assert_typed_value(actual, expected_result)
                effects.clear()
                native = Script(
                    hass,
                    await async_validate_actions_config(
                        hass, cv.SCRIPT_SCHEMA(raw_sequence)
                    ),
                    "Native oracle",
                    "script_probe",
                )
                try:
                    result = await native.async_run({})
                    assert effects == expected_effects
                    assert_typed_value(
                        result.variables["_function_result"], expected_result
                    )
                finally:
                    await native.async_stop()


async def test_reviewed_sparse_default_configuration_uses_genuine_ha_validation(hass):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        normalize_agent_config,
    )

    actual = normalize_agent_config({})
    for key, expected in {
        "chat_model": "gpt-5-mini",
        "api_mode": "auto",
        "memory_mode": "off",
        "temporary_memory": "off",
    }.items():
        assert actual[key] == expected
    assert actual["functions"]
    assert normalize_agent_config(actual) == actual


async def test_generated_native_waits_resume_only_after_the_requested_event(
    hass, monkeypatch
):
    """Native wait registration establishes ordering without elapsed-time sleeps."""
    effects = []

    async def mark(call):
        effects.append(call.data["marker"])

    hass.services.async_register("script_probe", "record", mark)
    for index in range(4):
        event = f"generated_script_gate_{index}"
        registered = asyncio.Event()
        original_listen = type(hass.bus).async_listen

        def observe_registration(
            bus,
            event_type,
            *args,
            original_listen=original_listen,
            event=event,
            registered=registered,
            **kwargs,
        ):
            remove = original_listen(bus, event_type, *args, **kwargs)
            if bus is hass.bus and event_type == event:
                registered.set()
            return remove

        # Observe native registration without replacing the listener or script.
        with monkeypatch.context() as registration_probe:
            registration_probe.setattr(
                type(hass.bus), "async_listen", observe_registration
            )
            await _assert_registered_wait(hass, event, index, effects, registered)


async def _assert_registered_wait(hass, event, index, effects, registered):
    sequence = cv.SCRIPT_SCHEMA(
        [
            {"variables": {"marker": index}},
            {
                "wait_for_trigger": [{"trigger": "event", "event_type": event}],
                "timeout": 30,
                "continue_on_timeout": False,
            },
            {"action": "script_probe.record", "data": {"marker": "{{ marker }}"}},
            {"variables": {"_function_result": "{{ wait.trigger.event.data.value }}"}},
        ]
    )
    pending = asyncio.create_task(
        ScriptFunction().execute(hass, {"sequence": sequence}, {}, None, [])
    )
    try:
        await asyncio.wait_for(registered.wait(), 10)
        assert hass.bus.async_listeners().get(event) == 1
        assert not pending.done()
        assert effects == list(range(index))
        hass.bus.async_fire(f"{event}_unrelated", {"value": "wrong"})
        await hass.async_block_till_done()
        assert not pending.done()
        hass.bus.async_fire(event, {"value": False})
        assert await asyncio.wait_for(pending, 10) is False
        assert effects == list(range(index + 1))
        assert event not in hass.bus.async_listeners()
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
