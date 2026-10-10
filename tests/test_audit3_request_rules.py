"""Native action shorthand and typed Function result regression contracts."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rules as rules,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from tests.test_audit_request_rules import (
    evaluate_functions,
    function_step,
    prepare_native,
)
from tests.test_request_rules import local_rule, manager


@pytest.mark.parametrize(
    "action",
    [
        {"if": "{{ true }}", "then": {"set_conversation_response": "SAFE"}},
        {"sequence": {"set_conversation_response": "SAFE"}},
        {"repeat": {"count": 1, "sequence": {"set_conversation_response": "SAFE"}}},
        {
            "choose": [
                {
                    "conditions": "{{ true }}",
                    "sequence": {"set_conversation_response": "SAFE"},
                }
            ]
        },
        {"choose": [], "default": {"set_conversation_response": "SAFE"}},
        {"parallel": {"sequence": {"variables": {"safe": True}}}},
    ],
)
@pytest.mark.parametrize("guest", [False, True])
async def test_native_shorthand_normalized_before_guest_checks(hass, action, guest):
    prepare_native(hass)
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [action]
    outcome = await rules.async_evaluate_rule(
        hass,
        await manager(rule),
        rules.RequestRuleRuntime(),
        "run",
        "session",
        guest_policy=GuestCapabilityPolicy(guest),
    )
    assert outcome.successful
    assert outcome.response in {"SAFE", "Done"}


@pytest.mark.parametrize("container", ["then", "sequence", "repeat"])
async def test_successful_stop_in_shorthand_container_is_success(hass, container):
    prepare_native(hass)
    stop = {"stop": "Finished successfully"}
    action = (
        {"if": "{{ true }}", "then": stop}
        if container == "then"
        else {"sequence": stop}
        if container == "sequence"
        else {"repeat": {"count": 1, "sequence": stop}}
    )
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [action]
    outcome = await rules.async_evaluate_rule(
        hass, await manager(rule), rules.RequestRuleRuntime(), "run", "session"
    )
    assert outcome.successful


async def test_authored_response_variable_and_alias_both_available(hass):
    hass.config.legacy_templates = False
    async def execute(*_args):
        return {"level": 62}

    outcome = await evaluate_functions(
        hass,
        [
            function_step(response_variable="reply"),
            {
                "set_conversation_response": "{{ reply.result.level }} / {{ lookup.level }}"
            },
        ],
        execute,
    )
    assert outcome.successful
    assert outcome.response == "62 / 62"


@pytest.mark.parametrize(
    "value", ["123456", "false", "null", '{"level":62}', False, 0, None, {"level": 62}]
)
async def test_result_alias_preserves_native_return_type(value):
    async def execute(*_args):
        return SimpleNamespace(tool_result={"result": value})

    results = {}
    executor = rules._ACTIVE_FUNCTION_EXECUTOR.set(execute)
    context = rules._ACTIVE_FUNCTION_RESULTS.set(results)
    try:
        captured = await rules.async_call_active_function("lookup", {}, "reading")
        assert captured == value
        assert type(captured) is type(value)
    finally:
        rules._ACTIVE_FUNCTION_RESULTS.reset(context)
        rules._ACTIVE_FUNCTION_EXECUTOR.reset(executor)
