"""Result references fail only when reached, and fail the whole native rule."""

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import request_rules as rules
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.test_request_rules import local_rule, manager


@pytest.mark.parametrize("consumer", ["if", "choose", "service"])
@pytest.mark.parametrize("value,reference", [({"status": "not_found"}, "lookup.missing"), ("plain text", "lookup.0"), ({}, "lookup.items"), ({}, "lookup.get"), ({}, "lookup.keys")])
async def test_invalid_reached_reference_cannot_run_fallback(tmp_path, consumer, value, reference):
    hass = HomeAssistant(str(tmp_path))
    effects = []

    async def capture(call):
        return {"result": await rules.async_call_active_function(call.data["function"], {}, call.data.get("result_alias"))}

    async def record(call):
        effects.append(call.data)

    async def execute(*_):
        return value

    hass.services.async_register(DOMAIN, "call_function", capture, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("review_probe", "record", record)
    effect = {"action": "review_probe.record", "data": {"message": "Fell through"}}
    template = "{" + reference + "}"
    condition = [{"condition": "template", "value_template": template}]
    next_action = {"if": condition, "then": [], "else": [effect], "continue_on_error": True} if consumer == "if" else ({"choose": [{"conditions": condition, "sequence": []}], "default": [effect], "continue_on_error": True} if consumer == "choose" else {**effect, "data": {"message": template}, "continue_on_error": True})
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [{"action": f"{DOMAIN}.call_function", "data": {"function": "lookup", "arguments": {}, "result_alias": "lookup"}}, next_action, effect]
    try:
        result = await rules.async_evaluate_rule(hass, await manager(rule), rules.RequestRuleRuntime(), "run", "session", function_executor=execute)
        assert not result.successful
        assert not effects
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("mode", ["disabled", "recoverable"])
async def test_bare_unavailable_alias_does_not_become_empty_service_data(hass, mode):
    from tests.test_audit_request_rules import evaluate_functions, function_step

    async def execute(*_):
        raise HomeAssistantError("recoverable")

    result = await evaluate_functions(hass, [function_step(**({"enabled": False} if mode == "disabled" else {"continue_on_error": True})), {"set_conversation_response": "{lookup}"}], execute)
    assert not result.successful


async def test_unreached_jinja_branch_does_not_resolve_missing_reference(hass):
    from tests.test_audit_request_rules import evaluate_functions, function_step

    async def execute(*_):
        return {"0": ["OK"]}

    result = await evaluate_functions(hass, [function_step(), {"if": "{{ false }}", "then": [{"set_conversation_response": "{lookup.missing}"}], "else": [{"set_conversation_response": "{lookup.0.0}"}]}], execute)
    assert result.successful and result.response == "OK"
