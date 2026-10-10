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


@pytest.mark.parametrize(
    "parallel",
    [
        {"stop": "Finished successfully"},
        [{"stop": "Finished successfully"}],
        [[{"stop": "Finished successfully"}]],
        [{"sequence": {"stop": "Finished successfully"}}],
    ],
)
@pytest.mark.parametrize("guest", [False, True])
async def test_parallel_stop_shorthand_succeeds(hass, parallel, guest):
    prepare_native(hass)
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [{"parallel": parallel}]
    outcome = await rules.async_evaluate_rule(
        hass,
        await manager(rule),
        rules.RequestRuleRuntime(),
        "run",
        "session",
        guest_policy=GuestCapabilityPolicy(guest),
    )
    assert outcome.successful


@pytest.mark.parametrize("guest", [False, True])
async def test_parallel_list_of_sequences_authorized_before_execution(hass, guest):
    prepare_native(hass)
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [
        {"parallel": [[{"set_conversation_response": "SAFE"}]]}
    ]
    outcome = await rules.async_evaluate_rule(
        hass,
        await manager(rule),
        rules.RequestRuleRuntime(),
        "run",
        "session",
        guest_policy=GuestCapabilityPolicy(guest),
    )
    assert outcome.successful
    assert outcome.response == "SAFE"


def test_parallel_normalization_preserves_templates_and_service_payload():
    action = {
        "alias": "Branch",
        "action": "notify.send_message",
        "data": {
            "sequence": {"text": "{{ message }}"},
            "parallel": [{"variables": {"token_index": 7}}],
        },
    }
    normalized = rules._normalize_script_containers([{"parallel": [action]}])
    assert normalized == [{"parallel": [{"sequence": [action]}]}]
    assert action["data"]["sequence"]["text"] == "{{ message }}"


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("value", ["123456", "True", "None", "[1,2]", '{"level":62}', "  spaced  ", False, 0, None, {"level": 62}, [1, 2]])
async def test_alias_assignment_preserves_type_in_real_script(hass, legacy, value):
    hass.config.legacy_templates = legacy

    async def execute(*_args):
        return value

    outcome = await evaluate_functions(hass, [
        function_step(response_variable="reply"),
        {"set_conversation_response": "{{ lookup == reply.result }} / {{ lookup is string }} / {{ reply.result is string }}"},
    ], execute)
    expected = "True" if isinstance(value, str) else "False"
    assert outcome.successful
    assert outcome.response == f"True / {expected} / {expected}"


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("value", ["123456", "False", "None", '{"level":62}', "  spaced  ", False, 0, None, {"level": 62}, [1, 2]])
def test_exact_result_reference_preserves_native_type_at_render_boundary(hass, legacy, value):
    from homeassistant.helpers.template import Template

    hass.config.legacy_templates = legacy
    helper = "__eoai_result_value_" + "a" * 32
    source = Template("{{ " + helper + "('answer') }}", hass)
    wrapped = rules._ResultReferenceTemplate(source, helper, lambda error: pytest.fail(str(error)))
    rendered = wrapped.async_render({"answer": value})
    assert rendered == value
    assert type(rendered) is type(value)


async def test_guest_parallel_forbidden_event_rejected_before_any_effect(hass):
    prepare_native(hass)
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [{"parallel": [{"sequence": [
        {"event": "audit_forbidden"},
    ]}]}]
    outcome = await rules.async_evaluate_rule(hass, await manager(rule), rules.RequestRuleRuntime(), "run", "session", guest_policy=GuestCapabilityPolicy(True))
    assert not outcome.successful
    hass.bus.async_fire_internal.assert_not_called()
    hass.services.async_call.assert_not_called()


def test_sensitive_parallel_action_is_detected():
    rule = local_rule()
    rule["action"]["actions"] = [{"parallel": [{"sequence": [
        {"action": "lock.unlock", "target": {"entity_id": "lock.front"}},
    ]}]}]
    assert rules.rule_has_sensitive_actions(rules.validate_rule(rule))


async def test_parallel_guest_preflight_prevents_partial_effects(hass):
    from homeassistant.core import Context
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    prepare_native(hass)
    policy = GuestCapabilityPolicy(True, configured_tool_names=frozenset({"lookup"}))
    effects = []

    async def execute(*_args):
        effects.append("effect")
        return "done"

    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [{"parallel": [{"sequence": [
        {"action": f"{DOMAIN}.call_function", "data": {"function": "lookup", "arguments": {}}},
        {"event": "forbidden"},
    ]}]}]
    outcome = await rules.async_evaluate_rule(hass, await manager(rule), rules.RequestRuleRuntime(), "run", "session", guest_policy=policy, live_guest_policy=lambda: policy, function_executor=execute, context=Context(), request_options={"functions": []})
    assert not outcome.successful
    assert not effects
    hass.services.async_call.assert_not_called()
