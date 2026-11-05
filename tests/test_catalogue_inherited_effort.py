"""Reset must retain explicit agent reasoning that becomes active on a route."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
)
from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as catalog_runtime,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
    RequestRuleRuntime,
    async_evaluate_rule,
    validate_rule_model_request,
)
from tests.test_catalogue_route_recognition import add_target, TARGET
from tests.test_model_catalog import MemoryStore
from tests.test_provider_catalog_route_reaudit import setup_manager
from tests.test_request_rule_routing_semantics import _rule


@pytest.fixture(autouse=True)
def isolate_catalog():
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["azure", "openai"])
@pytest.mark.parametrize(
    "mode",
    [
        "inherited",
        "prior_rule",
        "continued_prior",
        "continued_later",
        "request_prior",
        "disabled_prior",
        "disabled",
        "no_saved_effort",
        "bundled_target",
    ],
)
async def test_reset_retains_reasoning_activated_by_a_model_only_rule(
    hass, monkeypatch, api, provider, mode
):
    manager, options, _old, entry, candidate = setup_manager(
        hass, monkeypatch, TARGET, None
    )
    options.update(api_mode=api, reasoning_effort="high")
    if provider == "openai":
        entry.data = {"api_provider": provider}
        options.update(chat_model="gpt-4.1-mini")
        options.pop("azure_model")
    if mode in {
        "no_saved_effort",
        "prior_rule",
        "continued_prior",
        "continued_later",
        "request_prior",
        "disabled_prior",
    }:
        options.pop("reasoning_effort")
    target = "gpt-5" if mode == "bundled_target" else TARGET
    add_target(candidate, reasoning=True)
    manager.store.saved = {"catalog": candidate, "last_checked": 0}
    await manager.async_load()
    rule = _rule(
        "route",
        "route",
        action={
            "model": "new-deployment" if provider == "azure" else target,
            **({"azure_model": target} if provider == "azure" else {}),
            "scope": "conversation",
            "continue_to_ai": True,
        },
    )
    rule["enabled"] = mode != "disabled"
    configured_rules = [rule]
    if mode in {
        "prior_rule",
        "continued_prior",
        "continued_later",
        "request_prior",
        "disabled_prior",
    }:
        configured_rules.append(
            _rule(
                "high",
                "high",
                action={
                    "model": "gpt-5",
                    "reasoning_effort": "high",
                    "scope": "conversation",
                    "continue_to_ai": True,
                },
            )
        )
    if mode in {"request_prior", "disabled_prior"}:
        configured_rules[-1]["action"]["scope"] = (
            "request" if mode == "request_prior" else "conversation"
        )
        configured_rules[-1]["enabled"] = mode != "disabled_prior"
    if mode in {"continued_prior", "continued_later"}:
        configured_rules[-1].update(phrases=["route"], continue_matching=True, order=0)
        configured_rules[-1]["action"].update(scope="request", continue_to_ai=False)
        configured_rules[0]["order"] = 1
        if mode == "continued_later":
            configured_rules[0]["order"] = 0
            configured_rules[-1]["order"] = 1
    rules = RequestRules(MemoryStore({"rules": configured_rules}))
    await rules.async_initialize()
    monkeypatch.setattr(
        catalog_runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    validate_rule_model_request(rule, options, entry.data)
    base = build_provider_request_snapshot(
        options, entry.data, tools_required=False
    ).api_kwargs
    assert not {"reasoning", "reasoning_effort"} & base.keys()

    async def fresh():
        runtime = RequestRuleRuntime()
        if mode in {"prior_rule", "request_prior", "disabled_prior"}:
            await async_evaluate_rule(
                hass,
                rules,
                runtime,
                "high",
                "fresh",
                options["chat_model"],
                request_options=options,
                entry_data=entry.data,
            )
        outcome = await async_evaluate_rule(
            hass,
            rules,
            runtime,
            "route",
            "fresh",
            options["chat_model"],
            request_options=options,
            entry_data=entry.data,
        )
        effective = runtime.effective_options(
            options, "fresh", outcome.request_override
        )
        sent = build_provider_request_snapshot(
            effective, entry.data, tools_required=False
        ).api_kwargs
        return sent.get("reasoning", {}).get("effort", sent.get("reasoning_effort"))

    if mode != "disabled":
        before = await fresh()
        if mode not in {
            "no_saved_effort",
            "continued_later",
            "request_prior",
            "disabled_prior",
        }:
            assert before == "high"
    saved = deepcopy(manager.store.saved)
    generation = data.catalog_generation()
    result = await manager.async_reset()
    if mode in {"inherited", "prior_rule", "continued_prior"}:
        assert await fresh() == "high"
        assert result["source"] == "downloaded"
        assert "blocked" in result["last_error"]
        assert manager.store.saved == saved
        assert data.catalog_generation() == generation
    else:
        assert result["source"] == "bundled"
        if mode == "bundled_target":
            assert await fresh() == "high"


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_apply_checks_inherited_effort_without_overriding_explicit_route_effort(
    hass, monkeypatch, api, explicit
):
    manager, options, rule, entry, candidate = setup_manager(
        hass, monkeypatch, "gpt-5", None
    )
    options.update(api_mode=api, reasoning_effort="high")
    if explicit:
        rule["action"]["reasoning_effort"] = "low"
    target = next(item for item in candidate["models"] if item["id"] == "gpt-5")
    target["reasoning"]["by_api"][api]["efforts"].remove("high")
    for tool in target["tools"].values():
        tool[api] = {"support": "always"}
    data.validate_catalog(candidate)
    original = deepcopy(options), deepcopy(rule)
    before = data.catalog_generation()
    result = await manager.async_apply_update()
    assert result["source"] == ("downloaded" if explicit else "bundled")
    assert (data.catalog_generation() != before) == explicit
    assert (options, rule) == original
