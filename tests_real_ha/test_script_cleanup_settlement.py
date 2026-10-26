"""Real Script resources are released before repeated Assist cancellation exits."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses import request_rules
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.functions import (
    script as script_module,
)
from homeassistant.helpers import config_validation as cv, script as ha_script
from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_real_ha.test_request_rules_script_semantics import _local


def _capture_scripts(monkeypatch, module):
    original = module.Script
    scripts = []

    def capture(*args, **kwargs):
        script = original(*args, **kwargs)
        scripts.append(script)
        return script

    monkeypatch.setattr(module, "Script", capture)
    return scripts


def _assert_native_script_finished(hass, script):
    assert script.runs == 0
    # Older supported HA releases only expose async_stop. Exercise their public
    # cancellation behavior too; the registry assertion belongs to HA versions
    # that supply the native unload lifecycle API.
    if hasattr(ha_script.Script, "async_unload"):
        assert id(script) not in hass.data.get(ha_script.DATA_SCRIPTS, {})


@pytest.mark.parametrize("cancels", [1, 2, 3, 5, 10])
@pytest.mark.skipif(
    not hasattr(ha_script.Script, "async_unload"),
    reason="Native Script registry unloading is unavailable on this HA version",
)
async def test_native_runner_unregisters_after_repeated_cancellation(
    hass, monkeypatch, cancels
):
    scripts = _capture_scripts(monkeypatch, script_module)
    task = asyncio.create_task(
        script_module.ScriptFunction().execute(
            hass, {"sequence": cv.SCRIPT_SCHEMA([{"delay": 60}])}, {}, None, []
        )
    )
    try:
        async with asyncio.timeout(1):
            while not scripts or not scripts[0].is_running:
                await asyncio.sleep(0)
        for _ in range(cancels):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done()
        _assert_native_script_finished(hass, scripts[0])
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_result_reference_stop_cannot_skip_native_unload(
    hass, monkeypatch, api_mode, cancel
):
    effects = []

    async def effect(_call):
        effects.append(True)

    hass.services.async_register("script_probe", "record", effect)
    tool = {
        "spec": {
            "name": "result_probe",
            "description": "Result probe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "template",
            "value_template": "{}",
            "parse_result": True,
        },
    }
    agent = await _agent(hass, api_mode=api_mode, **{CONF_FUNCTION_TOOLS: [tool]})
    await agent._request_rules.async_create(
        _local(
            [
                {"action": "script_probe.record"},
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {
                        "function": "result_probe",
                        "arguments": {},
                        "result_alias": "battery",
                    },
                },
                {"wait_template": "{battery.missing}"},
            ]
        )
    )
    text = _chat_sse_text if api_mode == "chat_completions" else _responses_sse_text
    _install_wire(monkeypatch, agent, [text("Healthy")])
    scripts = _capture_scripts(monkeypatch, request_rules)
    original_render = request_rules._ResultReferenceTemplate.async_render_to_info
    errors = []
    task = None

    def observe(self, *args, **kwargs):
        try:
            return original_render(self, *args, **kwargs)
        except request_rules.RuleResponseValueError:
            errors.append(True)
            if cancel:
                # Observe the actual failure, without replacing native stop or
                # unload behavior, and cancel while its stop task is pending.
                asyncio.get_running_loop().call_soon(
                    task.cancel, "cancel while stopping invalid result reference"
                )
            raise

    monkeypatch.setattr(
        request_rules._ResultReferenceTemplate, "async_render_to_info", observe
    )
    task = asyncio.create_task(_say(hass, agent, "run rule"))
    try:
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert _speech(await task, successful=False) == "Failed safely"
        await hass.async_block_till_done()
        assert errors
        assert effects == [True]
        assert scripts
        for script in scripts:
            _assert_native_script_finished(hass, script)
        assert _speech(await _say(hass, agent, "independent healthy turn")) == "Healthy"
        assert effects == [True]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("route", ["function", "local_rule"])
@pytest.mark.parametrize("cancels", [3, 5, 10])
async def test_public_repeated_cancellation_unloads_without_repeating_effect(
    hass, monkeypatch, api_mode, route, cancels
):
    entered = asyncio.Event()
    effects = []

    async def effect(call):
        effects.append(call.context.id)
        entered.set()

    hass.services.async_register("script_probe", "effect", effect)
    sequence = [{"action": "script_probe.effect"}, {"delay": 60}]
    tool = {
        "spec": {
            "name": "perform_once",
            "description": "Execute once",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "script", "sequence": sequence},
    }
    agent = await _agent(
        hass,
        api_mode=api_mode,
        function_tool_error_recovery=False,
        **{CONF_FUNCTION_TOOLS: [tool] if route == "function" else []},
    )
    call = (
        _chat_sse_tool_call
        if api_mode == "chat_completions"
        else _responses_sse_tool_call
    )
    text = _chat_sse_text if api_mode == "chat_completions" else _responses_sse_text
    replies = [text("Healthy")]
    if route == "function":
        replies.insert(0, call("perform-once", "perform_once", {}))
    else:
        await agent._request_rules.async_create(_local(sequence, phrase="perform once"))
    _install_wire(monkeypatch, agent, replies)
    scripts = _capture_scripts(
        monkeypatch, script_module if route == "function" else request_rules
    )
    task = asyncio.create_task(_say(hass, agent, "perform once"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(cancels):
            task.cancel("cancel script after effect")
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done()
        assert len(effects) == 1
        assert len(scripts) == 1
        _assert_native_script_finished(hass, scripts[0])
        if route == "function":
            assert agent._unacknowledged_tool_schemas
            assert agent._unacknowledged_effects
        assert _speech(await _say(hass, agent, "independent healthy turn")) == "Healthy"
        assert len(effects) == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
