"""Prospective catalogue recognition must survive the next fresh Azure route."""

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
from homeassistant.exceptions import HomeAssistantError
from tests.test_model_catalog import MemoryStore
from tests.test_provider_catalog_route_reaudit import setup_manager
from tests.test_request_rule_routing_semantics import _rule

TARGET = "download-only-router-model"


@pytest.fixture(autouse=True)
def isolate_catalog():
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


def add_target(candidate, *, reasoning=False):
    target = deepcopy(
        next(
            item
            for item in candidate["models"]
            if item["id"] == ("gpt-5" if reasoning else "gpt-4.1-mini")
        )
    )
    target.update(id=TARGET, display_name="Downloaded target", status="current")
    for key in ("deprecated_at", "shutdown_at", "lifecycle_note"):
        target.pop(key, None)
    candidate["models"].append(target)
    data.validate_catalog(candidate)


async def fresh_route(hass, rules, options, entry):
    runtime = RequestRuleRuntime()
    result = await async_evaluate_rule(
        hass,
        rules,
        runtime,
        "route",
        "fresh-session",
        options["chat_model"],
        request_options=options,
        entry_data=entry.data,
    )
    effective = runtime.effective_options(
        options, "fresh-session", result.request_override
    )
    sent = build_provider_request_snapshot(
        effective, entry.data, tools_required=False
    ).api_kwargs
    assert sent["model"] == TARGET
    assert not {"reasoning", "reasoning_effort"} & sent.keys()
    return effective


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("scope", ["request", "conversation"])
@pytest.mark.parametrize("binding", ["implicit_route", "explicit_route", "configured"])
async def test_reset_preserves_recognition_for_a_fresh_azure_route(
    hass, monkeypatch, api, scope, binding
):
    manager, options, _old, entry, candidate = setup_manager(
        hass, monkeypatch, TARGET, None
    )
    options["api_mode"] = api
    add_target(candidate)
    manager.store.saved = {"catalog": candidate, "last_checked": 0}
    await manager.async_load()
    if binding == "configured":
        options.update(chat_model=TARGET, azure_model="")
    rule = _rule(
        "route",
        "route",
        action={
            "model": TARGET,
            "scope": scope,
            "continue_to_ai": True,
            **({"azure_model": TARGET} if binding == "explicit_route" else {}),
        },
    )
    rules = RequestRules(MemoryStore({"rules": [rule]}))
    await rules.async_initialize()
    monkeypatch.setattr(
        catalog_runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    validate_rule_model_request(rule, options, entry.data)
    await fresh_route(hass, rules, options, entry)
    saved = deepcopy(manager.store.saved)
    generation = data.catalog_generation()
    result = await manager.async_reset()
    # A new conversation must still be able to resolve and dispatch this route.
    await fresh_route(hass, rules, options, entry)
    if binding == "implicit_route":
        assert result["source"] == "downloaded"
        assert "blocked" in result["last_error"]
        assert data.catalog_generation() == generation
        assert manager.store.saved == saved
    else:
        assert result["source"] == "bundled"
        assert not result["last_error"]


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
async def test_apply_can_use_a_newly_recognized_exact_azure_model(
    hass, monkeypatch, api
):
    manager, options, _old, entry, candidate = setup_manager(
        hass, monkeypatch, TARGET, None
    )
    options["api_mode"] = api
    add_target(candidate)
    rule = _rule(
        "route",
        "route",
        action={"model": TARGET, "scope": "conversation", "continue_to_ai": True},
    )
    rules = RequestRules(MemoryStore({"rules": [rule]}))
    await rules.async_initialize()
    monkeypatch.setattr(
        catalog_runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    with pytest.raises(HomeAssistantError, match="underlying Azure model"):
        validate_rule_model_request(rule, options, entry.data)
    assert (await manager.async_apply_update())["source"] == "downloaded"
    validate_rule_model_request(rule, options, entry.data)
    await fresh_route(hass, rules, options, entry)


@pytest.mark.parametrize(
    "provider,binding", [("azure", False), ("azure", True), ("openai", False)]
)
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
async def test_captured_effort_keeps_its_static_model_requirements(
    hass, monkeypatch, provider, binding, api
):
    manager, options, _old, entry, candidate = setup_manager(
        hass, monkeypatch, TARGET, None
    )
    options["api_mode"] = api
    if provider == "openai":
        entry.data = {"api_provider": "openai"}
        options.pop("azure_model")
    add_target(candidate, reasoning=True)
    manager.store.saved = {"catalog": candidate, "last_checked": 0}
    await manager.async_load()
    rule = _rule(
        "route",
        "route {effort=low|high}",
        match_type="sentence_pattern",
        action={
            "model": TARGET,
            "reasoning_effort": "{effort}",
            "scope": "conversation",
            "continue_to_ai": True,
            **({"azure_model": TARGET} if binding else {}),
        },
    )
    rules = RequestRules(MemoryStore({"rules": [rule]}))
    await rules.async_initialize()
    monkeypatch.setattr(
        catalog_runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    validate_rule_model_request(rule, options, entry.data)

    async def call(effort):
        runtime = RequestRuleRuntime()
        result = await async_evaluate_rule(
            hass,
            rules,
            runtime,
            f"route {effort}",
            "new-session",
            options["chat_model"],
            request_options=options,
            entry_data=entry.data,
        )
        effective = runtime.effective_options(
            options, "new-session", result.request_override
        )
        sent = build_provider_request_snapshot(
            effective, entry.data, tools_required=False
        ).api_kwargs
        assert (
            sent.get("reasoning", {}).get("effort", sent.get("reasoning_effort"))
            == effort
        )

    await call("high")
    generation = data.catalog_generation()
    result = await manager.async_reset()
    await call("low")
    await call("high")
    assert result["source"] == "downloaded"
    assert "blocked" in result["last_error"]
    assert data.catalog_generation() == generation


@pytest.mark.parametrize("provider", ["azure", "openai"])
async def test_already_unsupported_captured_effort_does_not_newly_block_reset(
    hass, monkeypatch, provider
):
    manager, options, _old, entry, candidate = setup_manager(
        hass, monkeypatch, TARGET, None
    )
    if provider == "openai":
        entry.data = {"api_provider": "openai"}
        options.pop("azure_model")
    add_target(candidate)
    manager.store.saved = {"catalog": candidate, "last_checked": 0}
    await manager.async_load()
    rule = _rule(
        "route",
        "route {effort}",
        match_type="sentence_pattern",
        action={
            "model": TARGET,
            "reasoning_effort": "{effort}",
            "scope": "conversation",
            "continue_to_ai": True,
            **({"azure_model": TARGET} if provider == "azure" else {}),
        },
    )
    rules = RequestRules(MemoryStore({"rules": [rule]}))
    await rules.async_initialize()
    monkeypatch.setattr(
        catalog_runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    assert (await manager.async_reset())["source"] == "bundled"
