"""Function dependency and result contracts follow HA's native payload merge."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_dependency_integrity as integrity,
    management_ui,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
)
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script, async_validate_actions_config
from tests.test_function_dependency_integrity import _tool
from tests.test_request_rules import MemoryStore, local_rule, manager

SERVICE = "extended_openai_conversation_responses.call_function"


def tools():
    result = []
    for name in ("echo", "actual_b", "renamed"):
        tool = _tool(
            {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            }
        )
        tool["spec"]["name"] = name
        result.append(tool)
    return result


def rule_for(step):
    rule = local_rule("Payload", ["echo now"])
    rule["action"]["actions"] = [deepcopy(step)]
    return rr.validate_rule(rule)


@pytest.mark.parametrize(
    "shape", ["template_only", "split", "override_name", "override_arguments"]
)
async def test_effective_payload_validation_and_dependencies(hass, shape):
    step = {
        "action": SERVICE,
        "data": {"function": "echo", "arguments": {"message": "static"}},
    }
    target = "echo"
    if shape == "template_only":
        step = {
            "action": SERVICE,
            "data_template": {
                "function": "echo",
                "arguments": {"message": "{{ 'hello' }}"},
            },
        }
    elif shape == "split":
        step["data"].pop("arguments")
        step["data_template"] = {"arguments": {"message": "{{ 'hello' }}"}}
    elif shape == "override_name":
        step["data_template"] = {"function": "actual_b"}
        target = "actual_b"
    else:
        step["data_template"] = {"arguments": {}}
    rule = rule_for(step)
    if shape == "override_arguments":
        with pytest.raises(HomeAssistantError, match="message"):
            await integrity.async_validate_request_rule_functions(hass, rule, tools())
        return
    selected = management_ui._request_rule_function_tools(rule, {"functions": tools()})
    assert [item["spec"]["name"] for item in selected] == [target]
    await integrity.async_validate_request_rule_functions(hass, rule, selected)
    manager = rr.RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    assert integrity.recursive_function_references(manager, target) == [
        {"id": rule["id"], "name": "Payload"}
    ]
    assert not integrity.recursive_function_references(
        manager, "actual_b" if target == "echo" else "echo"
    )
    preview = request_rule_match_preview(await manager.async_match(hass, "echo now"))
    assert preview["would_do"]["functions"][0]["name"] == target


async def test_rename_updates_winning_mapping_and_preserves_templates(hass):
    step = {
        "action": SERVICE,
        "data": {"function": "echo", "arguments": {"message": "{{ 'hello' }}"}},
        "data_template": {"function": "actual_b"},
    }
    manager = rr.RequestRules(MemoryStore({"rules": [rule_for(step)]}))
    await manager.async_initialize()
    assert (
        await integrity.async_rename_function_reference_recursive(
            manager, "actual_b", "renamed"
        )
        == 1
    )
    saved = manager._rules[0]["action"]["actions"][0]
    assert saved["data"] == step["data"]
    assert saved["data_template"] == {"function": "renamed"}
    assert manager.function_references("renamed")
    assert not manager.function_references("actual_b")


async def test_native_script_dispatches_shallow_merged_payload(hass):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **kw: (
        asyncio.create_task(coro)
    )
    hass.services.supports_response.return_value = SupportsResponse.OPTIONAL
    actions = await async_validate_actions_config(
        hass,
        cv.SCRIPT_SCHEMA(
            [
                {
                    "action": SERVICE,
                    "data": {"function": "echo", "arguments": {"message": "discarded"}},
                    "data_template": {
                        "function": "actual_b",
                        "arguments": {"message": "{{ 'hello' }}"},
                    },
                }
            ]
        ),
    )
    script = Script(hass, actions, "Payload contract", "test")
    await script.async_run(context=Context())
    call = hass.services.async_call.call_args
    assert call.kwargs["domain"] == "extended_openai_conversation_responses"
    assert call.kwargs["service"] == "call_function"
    assert call.kwargs["service_data"] == {
        "function": "actual_b",
        "arguments": {"message": "hello"},
    }


def test_result_alias_and_step_identity_use_effective_payload(hass):
    raw = local_rule("Result", ["echo now"])
    raw["action"]["actions"] = [
        {
            "action": SERVICE,
            "data": {
                "function": "echo",
                "arguments": {"message": "hello"},
                "result_alias": "shadowed",
            },
            "data_template": {"result_alias": "answer"},
        }
    ]
    raw["action"]["success_response"] = "{answer}"
    assigned = rr._assign_missing_result_step_ids(raw)
    rule = rr.validate_rule(assigned)
    step = rule["action"]["actions"][0]
    assert len(step["data_template"]["step_id"]) == 32
    assert "step_id" not in step["data"]
    bindings = {}
    sequence = rr._native_result_sequence([step], {}, bindings)
    assert next(iter(bindings.values()))[0] == "answer"
    assert sequence[0]["response_variable"]
    assert "answer" in sequence[1]["variables"]


async def test_native_rule_dispatch_and_result_follow_effective_payload(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    calls = []

    async def capture(call):
        return {
            "result": await rr.async_call_active_function(
                call.data["function"],
                call.data.get("arguments", {}),
                call.data.get("result_alias"),
            )
        }

    async def execute(name, arguments):
        calls.append((name, arguments))
        return {"value": arguments["message"]}

    hass.services.async_register(
        "extended_openai_conversation_responses",
        "call_function",
        capture,
        supports_response=SupportsResponse.ONLY,
    )
    rule = local_rule("Payload", ["echo now"])
    rule["action"]["actions"] = [
        {
            "action": SERVICE,
            "data": {
                "function": "echo",
                "arguments": {"message": "discarded"},
                "result_alias": "shadowed",
            },
            "data_template": {
                "function": "actual_b",
                "arguments": {"message": "{{ 'hello' }}"},
                "result_alias": "answer",
            },
        }
    ]
    rule["action"]["success_response"] = "Value {answer.value}"
    try:
        result = await rr.async_evaluate_rule(
            hass,
            await manager(rule),
            rr.RequestRuleRuntime(),
            "echo now",
            "session",
            function_executor=execute,
        )
        assert result.successful
        assert result.response == "Value hello"
        assert calls == [("actual_b", {"message": "hello"})]
    finally:
        await hass.async_stop(force=True)


async def test_whole_arguments_template_defers_only_runtime_validation(hass):
    rule = rule_for(
        {
            "action": SERVICE,
            "data_template": {
                "function": "echo",
                "arguments": "{{ dict(message='hello') }}",
            },
        }
    )
    await integrity.async_validate_request_rule_functions(hass, rule, tools())
    bad = deepcopy(rule)
    bad["action"]["actions"][0]["data_template"]["arguments"] = "not an object"
    with pytest.raises(HomeAssistantError, match="arguments must be an object"):
        await integrity.async_validate_request_rule_functions(hass, bad, tools())
    unknown = deepcopy(rule)
    unknown["action"]["actions"][0]["data_template"] = "{{ dict(function='actual_b') }}"
    with pytest.raises(HomeAssistantError, match="Function action is invalid"):
        await integrity.async_validate_request_rule_functions(hass, unknown, tools())


@pytest.mark.parametrize("winning", ["allowed", "disabled"])
async def test_guest_preflight_checks_winning_function(hass, winning):
    configured = []
    for name in ("allowed", "disabled"):
        tool = _tool()
        tool["spec"]["name"] = name
        tool["function"]["name"] = "get_history"
        tool["enabled"] = name == "allowed"
        configured.append(tool)
    action = {
        "action": SERVICE,
        "data": {"function": "allowed" if winning == "disabled" else "disabled"},
        "data_template": {"function": winning},
    }
    assert await rr._async_guest_functions_allowed(
        hass, [action], GuestCapabilityPolicy(True), {}, {"functions": configured}
    ) is (winning == "allowed")
