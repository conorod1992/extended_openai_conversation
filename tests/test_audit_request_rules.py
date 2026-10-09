"""Audit reproductions against Home Assistant's real native Script engine."""

import asyncio

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import request_rules as module
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.function_groups import FunctionGroupRuntime
from custom_components.extended_openai_conversation_responses.request_rule_patterns import compile_sentence_pattern, SentencePatternError
from tests.test_request_rules import local_rule, manager
from tests.test_request_rules_native_script import evaluate


def prepare_native(hass):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **kwargs: asyncio.create_task(coro)
    hass.services.supports_response.return_value = SupportsResponse.OPTIONAL


def function_step(**extra):
    return {"service": f"{DOMAIN}.call_function", "data": {"function": "lookup", "arguments": {}, "result_alias": "lookup"}, **extra}


async def evaluate_functions(hass, actions, executor):
    prepare_native(hass)
    async def dispatch(domain, service, service_data, **kwargs):
        return {"result": await module.async_call_active_function(service_data["function"], service_data.get("arguments", {}), service_data.get("result_alias"))}
    hass.services.async_call.side_effect = dispatch
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = actions
    return await module.async_evaluate_rule(hass, await manager(rule), module.RequestRuleRuntime(), "run", "session", function_executor=executor)


async def test_unreachable_missing_result_field_does_not_abort(hass):
    async def execute(*args):
        return {"status": "not_found"}
    result = await evaluate_functions(hass, [function_step(), {"choose": [{"conditions": "{{ false }}", "sequence": [{"set_conversation_response": "{lookup.value}"}]}], "default": [{"set_conversation_response": "Nothing found"}]}], execute)
    assert result.successful and result.response == "Nothing found"


@pytest.mark.parametrize("mode", ["disabled", "recoverable"])
async def test_skipped_or_recoverable_capture_allows_later_actions(hass, mode):
    async def execute(*args):
        raise HomeAssistantError("recoverable")
    step = function_step(**({"enabled": False} if mode == "disabled" else {"continue_on_error": True}))
    result = await evaluate_functions(hass, [step, {"set_conversation_response": "Recovered"}], execute)
    assert result.successful and result.response == "Recovered"


async def test_stop_with_undefined_response_is_failure(hass):
    prepare_native(hass)
    result = await evaluate(hass, [{"stop": "Done", "response_variable": "missing"}])
    assert not result.successful


@pytest.mark.parametrize("action", [{"if": "{{ true }}", "then": [{"set_conversation_response": "Accepted"}]}, {"repeat": {"while": "{{ false }}", "sequence": [{"variables": {"x": 1}}]}}, {"repeat": {"until": "{{ true }}", "sequence": [{"variables": {"x": 1}}]}}])
async def test_native_shorthand_survives_validation(hass, action):
    prepare_native(hass)
    result = await evaluate(hass, [action])
    assert result.successful


@pytest.mark.parametrize("name", ["request", "true", "none", "repeat"])
def test_reserved_capture_names_rejected(name):
    with pytest.raises(SentencePatternError):
        compile_sentence_pattern("ask {" + name + "}")


async def test_service_spelling_validates_result_aliases(hass):
    rule = local_rule()
    rule["action"]["actions"] = [function_step()]
    rule["action"]["success_response"] = "{lookup.value}"
    assert module.validate_rule(rule)["action"]["success_response"] == "{lookup.value}"
    rule["action"]["actions"][0]["data"]["result_alias"] = "request"
    with pytest.raises(ValueError):
        module.validate_rule(rule)


async def test_native_rule_error_log_excludes_exception_chain(hass, caplog):
    async def execute(*args):
        try:
            raise ValueError("https://example.test/?api_key=private-audit-secret")
        except ValueError as err:
            raise HomeAssistantError("Request failed") from err
    result = await evaluate_functions(hass, [function_step()], execute)
    assert not result.successful
    assert "private-audit-secret" not in caplog.text


def test_new_conversation_generation_never_inherits_scoped_state():
    old = module.request_rule_session_id("device:kitchen", "old-conversation")
    new = module.request_rule_session_id("device:kitchen", "new-conversation")
    routes = module.RequestRuleRuntime()
    routes.set(old, {"chat_model": "gpt-4.1"})
    routes.get(old)  # A failed request may still read old state.
    groups = FunctionGroupRuntime()
    groups.begin(old, 5).loaded_group_ids.add("private-group")
    groups.begin(old, 5)
    assert routes.get(new) == {}
    assert groups.begin(new, 5).loaded_group_ids == set()
