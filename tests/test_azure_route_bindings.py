"""An Azure deployment switch cannot borrow another deployment's model metadata."""

import pytest

from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    validate_rule_model_request,
    validate_rule,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_request_rule_routing_semantics import MemoryStore, _rule


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("scope", ["request", "conversation"])
@pytest.mark.parametrize("alias", [False, True])
async def test_route_dispatch_and_reset_keep_binding_pairs(hass, api, scope, alias):
    defaults = {
        "chat_model": "main-deployment",
        "azure_model": "gpt-5",
        "api_mode": api,
        "functions": [],
        "memory_mode": "off",
        "temporary_memory": "off",
        "max_tokens": 8192,
    }
    entry = {"api_provider": "azure"}
    destination = "cheap-deployment" if alias else "gpt-4.1-mini"
    action = {
        "model": destination,
        "scope": scope,
        "continue_to_ai": True,
        "reasoning_effort": None,
        **({"azure_model": "gpt-4.1-mini"} if alias else {}),
    }
    route = validate_rule(
        _rule("cheap", "route", match_type="starts_with", action=action)
    )
    validate_rule_model_request(route, defaults, entry)
    rules = RequestRules(MemoryStore({"rules": [route]}))
    await rules.async_initialize()
    runtime = RequestRuleRuntime()
    outcome = await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "route now",
        "session",
        "main-deployment",
        request_options=defaults,
        entry_data=entry,
    )
    effective = runtime.effective_options(defaults, "session", outcome.request_override)
    assert effective["chat_model"] == destination
    assert effective["azure_model"] == "gpt-4.1-mini"
    sent = build_provider_request_snapshot(
        effective, entry, tools_required=False
    ).api_kwargs
    assert sent["model"] == destination
    assert not {"reasoning", "reasoning_effort", "include"} & sent.keys()
    later = runtime.effective_options(defaults, "session")
    assert later["azure_model"] == (
        "gpt-4.1-mini" if scope == "conversation" else "gpt-5"
    )
    reset = validate_rule(
        _rule(
            "reset",
            "reset",
            action={"scope": "conversation", "reset": True, "continue_to_ai": True},
        )
    )
    await rules.async_replace_backup({"rules": [reset]})
    await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "reset",
        "session",
        "main-deployment",
        request_options=defaults,
        entry_data=entry,
    )
    assert runtime.effective_options(defaults, "session")["azure_model"] == "gpt-5"


async def test_unknown_alias_is_rejected_before_retaining_route(hass):
    defaults = {
        "chat_model": "main",
        "azure_model": "gpt-5",
        "api_mode": "responses",
        "functions": [],
        "memory_mode": "off",
    }
    route = validate_rule(
        _rule(
            "cheap", "route", action={"model": "unknown-alias", "scope": "conversation"}
        )
    )
    with pytest.raises(HomeAssistantError, match="underlying Azure model"):
        validate_rule_model_request(route, defaults, {"api_provider": "azure"})
    rules = RequestRules(MemoryStore({"rules": [route]}))
    await rules.async_initialize()
    runtime = RequestRuleRuntime()
    with pytest.raises(HomeAssistantError, match="underlying Azure model"):
        await async_evaluate_rule(
            hass,
            rules,
            runtime,
            "route",
            "session",
            "main",
            request_options=defaults,
            entry_data={"api_provider": "azure"},
        )
    assert runtime.get("session") == {}


def test_same_deployment_reuses_its_own_configured_binding():
    defaults = {
        "chat_model": "main",
        "azure_model": "gpt-5",
        "api_mode": "responses",
        "functions": [],
        "memory_mode": "off",
    }
    route = validate_rule(
        _rule(
            "same",
            "same",
            action={
                "model": "main",
                "reasoning_effort": "high",
                "scope": "conversation",
            },
        )
    )
    validate_rule_model_request(route, defaults, {"api_provider": "azure"})


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("first_reset", [False, True])
@pytest.mark.parametrize("second_scope", ["request", "conversation"])
async def test_chained_azure_routes_replace_the_whole_binding(
    hass, api, first_reset, second_scope
):
    defaults = {
        "chat_model": "main",
        "azure_model": "gpt-5",
        "api_mode": api,
        "functions": [],
        "memory_mode": "off",
    }
    first = _rule(
        "first",
        "route",
        match_type="starts_with",
        action={
            "reset": first_reset,
            "scope": "request",
            "continue_to_ai": False,
            **({} if first_reset else {"model": "first-alias", "azure_model": "gpt-5"}),
        },
    )
    first["continue_matching"] = True
    second = _rule(
        "second",
        "route",
        match_type="starts_with",
        order=1,
        action={
            "model": "second-alias",
            "azure_model": "gpt-4.1-mini",
            "scope": second_scope,
            "continue_to_ai": True,
        },
    )
    rules = RequestRules(MemoryStore({"rules": [first, second]}))
    await rules.async_initialize()
    runtime = RequestRuleRuntime()
    runtime.set("session", {"chat_model": "retained-alias", "azure_model": "gpt-5"})
    result = await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "route now",
        "session",
        "main",
        request_options=defaults,
        entry_data={"api_provider": "azure"},
    )
    effective = runtime.effective_options(defaults, "session", result.request_override)
    assert effective["chat_model"] == "second-alias"
    assert effective["azure_model"] == "gpt-4.1-mini"
    sent = build_provider_request_snapshot(
        effective, {"api_provider": "azure"}, tools_required=False
    ).api_kwargs
    assert not {"reasoning", "reasoning_effort", "include"} & sent.keys()
    later = runtime.effective_options(defaults, "session")
    assert later["azure_model"] == (
        "gpt-4.1-mini" if second_scope == "conversation" else "gpt-5"
    )


async def test_dynamic_azure_binding_and_later_effort_reuse_its_deployment(hass):
    defaults = {
        "chat_model": "main",
        "azure_model": "gpt-4.1-mini",
        "api_mode": "responses",
        "functions": [],
        "memory_mode": "off",
    }
    route = validate_rule(
        _rule(
            "dynamic",
            "route {deployment} using {underlying}",
            match_type="sentence_pattern",
            action={
                "model": "{deployment}",
                "azure_model": "{underlying}",
                "scope": "conversation",
            },
        )
    )
    validate_rule_model_request(route, defaults, {"api_provider": "azure"})
    rules = RequestRules(MemoryStore({"rules": [route]}))
    await rules.async_initialize()
    runtime = RequestRuleRuntime()
    await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "route custom-deployment using gpt-5",
        "session",
        "main",
        request_options=defaults,
        entry_data={"api_provider": "azure"},
    )
    effort = _rule(
        "effort",
        "high",
        action={
            "model": "custom-deployment",
            "reasoning_effort": "high",
            "scope": "conversation",
        },
    )
    await rules.async_replace_backup({"rules": [effort]})
    await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "high",
        "session",
        "main",
        request_options=defaults,
        entry_data={"api_provider": "azure"},
    )
    assert runtime.get("session") == {
        "chat_model": "custom-deployment",
        "azure_model": "gpt-5",
        "reasoning_effort": "high",
    }


def test_underlying_binding_is_not_accepted_for_non_azure_route():
    route = validate_rule(
        _rule(
            "invalid",
            "route",
            action={"model": "gpt-5", "azure_model": "gpt-5", "scope": "conversation"},
        )
    )
    with pytest.raises(HomeAssistantError, match="only supported for Azure"):
        validate_rule_model_request(
            route, {"chat_model": "gpt-4.1-mini"}, {"api_provider": "openai"}
        )
