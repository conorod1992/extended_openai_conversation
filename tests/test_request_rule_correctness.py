"""Request Rule outcome, template, routing and Repair regressions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_lifecycle,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.intent import IntentResponseErrorCode
from tests.test_request_rules import MemoryStore, routing_rule


@pytest.mark.parametrize("slots", [{}, {"name": "Kitchen"}])
def test_returned_braces_remain_opaque(slots):
    results = {"lookup": {"text": "Use {name} and {missing.path}"}}
    assert (
        rr.resolve_result_values("Result: {lookup.text}", slots, results)
        == "Result: Use {name} and {missing.path}"
    )
    assert (
        rr.resolve_result_values("{lookup.text}", slots, results)
        == results["lookup"]["text"]
    )


@pytest.mark.parametrize(
    "target",
    [
        {"entity_id": "{{ 'light.kitchen' }}"},
        {"entity_id": ["{{ 'light.kitchen' }}"]},
        "{{ {'entity_id': 'light.kitchen'} }}",
    ],
)
def test_dynamic_native_target_passes_static_rule_validation(target):
    actions = [{"action": "light.turn_on", "target": target}]
    assert rr._validate_script_sequence(actions) == actions


@pytest.mark.parametrize("successful", [True, False])
def test_local_rule_result_reports_machine_readable_outcome(successful):
    agent = SimpleNamespace(
        entity_id="conversation.test",
        subentry=SimpleNamespace(data={}),
        _fire_conversation_finished=Mock(),
    )
    user_input = SimpleNamespace(language="en")
    chat_log = SimpleNamespace(content=[], conversation_id="session")
    result = ExtendedOpenAIAgentEntity._local_rule_result(
        agent,
        user_input,
        chat_log,
        "Done" if successful else "Failed",
        successful=successful,
    )
    assert result.response.error_code == (
        None if successful else IntentResponseErrorCode.UNKNOWN
    )
    assert agent._fire_conversation_finished.call_args.kwargs["status"] == (
        "local" if successful else "error"
    )
    if not successful:
        assert result.continue_conversation is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"chat_model": "gpt-5-pro", "web_search": True},
        {"chat_model": "gpt-6-astra", "api_mode": "chat_completions"},
        {"chat_model": "gpt-4o", "max_tokens": 9999999},
    ],
)
def test_static_route_validates_inherited_request(overrides):
    rule = {
        "action_type": "model_routing",
        "action": {"model": overrides["chat_model"]},
    }
    with pytest.raises(HomeAssistantError):
        rr.validate_rule_model_request(rule, overrides, {})


async def test_invalid_captured_route_does_not_poison_conversation_state(hass):
    rule = routing_rule(scope="conversation")
    rule["action"]["model"] = "{model}"
    rule["action"]["reasoning_effort"] = None
    rule["phrases"] = ["route {model}"]
    rule["match_type"] = "sentence_pattern"
    rules = rr.RequestRules(MemoryStore())
    await rules.async_initialize()
    await rules.async_create(rule)
    runtime = rr.RequestRuleRuntime()
    with pytest.raises(HomeAssistantError):
        await rr.async_evaluate_rule(
            hass,
            rules,
            runtime,
            "route gpt-5-pro",
            "session",
            request_options={"chat_model": "gpt-5.6", "web_search": True},
            entry_data={},
        )
    assert runtime.get("session", 30) == {}


@pytest.mark.parametrize(
    "mode", ["deleted", "disabled", "changed", "unchanged", "captured"]
)
async def test_rule_override_repair_reconciles_committed_rules(hass, monkeypatch, mode):
    rules = rr.RequestRules(MemoryStore())
    await rules.async_initialize()
    rule = routing_rule(scope="conversation")
    rule["action"]["model"] = "gpt-5.1"
    if mode != "deleted":
        if mode == "disabled":
            rule["enabled"] = False
        if mode == "changed":
            rule["action"]["model"] = "gpt-5.6"
        if mode == "captured":
            rule["action"]["model"] = "{model}"
            rule["match_type"] = "sentence_pattern"
            rule["phrases"] = ["route {model}"]
        await rules.async_create(rule)
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        title="Assistant",
        data={"chat_model": "gpt-5.6"},
    )
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.data[rr._MANAGERS] = {("entry", "agent"): rules}
    hass.data[model_lifecycle._DATA_FAILURES] = {
        ("entry", "agent"): {"configured_model": "gpt-5.6", "model": "gpt-5.1"}
    }
    monkeypatch.setattr(
        model_lifecycle,
        "lifecycle_snapshot",
        lambda model, **_kwargs: {
            "model": model,
            "status": "deprecated" if model == "gpt-5.1" else "current",
            "shutdown_reached": model == "gpt-5.1",
        },
    )
    monkeypatch.setattr(
        model_lifecycle.ir,
        "async_get",
        lambda _hass: SimpleNamespace(async_get_issue=lambda *_args: None),
    )
    delete = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_delete_issue", delete)
    model_lifecycle.sync_entry_model_lifecycle(hass, entry)
    assert delete.called == (mode in {"deleted", "disabled", "changed"})
