"""Native action shorthand and typed Function result regression contracts."""



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


async def test_authored_response_variable_feeds_conversation_response(hass):
    hass.config.legacy_templates = False

    async def execute(*_args):
        return {"level": 62}

    outcome = await evaluate_functions(
        hass,
        [
            function_step(response_variable="reply"),
            {
                "set_conversation_response": "{{ reply.result.level }}"
            },
        ],
        execute,
    )
    assert outcome.successful
    assert outcome.response == "62"


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
async def test_response_variable_preserves_type_in_real_script(hass, legacy, value):
    hass.config.legacy_templates = legacy

    async def execute(*_args):
        return value

    outcome = await evaluate_functions(hass, [
        function_step(response_variable="reply"),
        {"set_conversation_response": "{{ reply.result is string }}"},
    ], execute)
    expected = "True" if isinstance(value, str) else "False"
    assert outcome.successful
    assert outcome.response == expected


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("value", ["123456", '{"level":62}', False, 0, None, {"level": 62}])
async def test_exact_reference_preserves_type_in_script_with_live_privacy_guard(hass, legacy, value):
    hass.config.legacy_templates = legacy
    prepare_native(hass)
    captured = []

    async def execute(*_args):
        return value

    async def dispatch(domain, service, service_data, **_kwargs):
        data = service_data
        if service == "call_function":
            return {"result": await rules.async_call_active_function(data["function"], data.get("arguments", {}))}
        if domain == "rule_probe":
            captured.append(data["message"])
        return None

    hass.services.async_call.side_effect = dispatch
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [function_step(response_variable="reply"), {"action": "rule_probe.record", "data": {"message": "{{ reply.result is string }}"}}]
    configured = await manager()
    await configured.async_create(rule)
    outcome = await rules.async_evaluate_rule(hass, configured, rules.RequestRuleRuntime(), "run", "session", function_executor=execute, live_guest_policy=GuestCapabilityPolicy.unrestricted)
    assert outcome.successful
    assert captured == [str(isinstance(value, str)) if legacy else isinstance(value, str)]


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



@pytest.mark.parametrize("level", [0, 62])
async def test_function_response_feeds_native_branch_and_speech(tmp_path, level):
    from custom_components.extended_openai_conversation_responses import services
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant(str(tmp_path))
    hass.config.legacy_templates = False
    effects = []

    async def execute(*_args):
        return {"level": level}

    async def record(call):
        effects.append(call.service)

    await services.async_setup_services(hass, {})
    hass.services.async_register("rule_probe", "record", record)
    rule = local_rule(phrases=["run"])
    producer = function_step(response_variable="reading")
    rule["action"].update(
        actions=[
            producer,
            {
                "if": [{"condition": "template", "value_template": "{{ reading.result.level < 20 }}"}],
                "then": [{"action": "rule_probe.record"}],
            },
            {"set_conversation_response": "Battery {{ reading.result.level }}"},
        ],
    )
    try:
        configured = await manager()
        await configured.async_create(rule)
        outcome = await rules.async_evaluate_rule(
            hass, configured, rules.RequestRuleRuntime(), "run", "session",
            function_executor=execute,
        )
        assert outcome.successful
        assert outcome.response == f"Battery {level}"
        assert effects == (["record"] if level < 20 else [])
    finally:
        await hass.async_stop(force=True)
