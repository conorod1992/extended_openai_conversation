"""Catalogue publication must use the same routed provider profile as dispatch."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
)
from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule_model_request,
)
from tests.test_model_catalog import MemoryStore


@pytest.fixture(autouse=True)
def isolate_catalog():
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


def setup_manager(hass, monkeypatch, identity, effort):
    options = {
        "chat_model": "base-deployment",
        "azure_model": "gpt-4.1-mini",
        "api_mode": "responses",
        "functions": [],
        "max_function_calls_per_conversation": 0,
        "memory_mode": "off",
        "temporary_memory": "off",
        "continue_conversation": "ha_default",
        "max_tokens": 512,
    }
    rule = {
        "enabled": True,
        "action_type": "model_routing",
        "action": {
            "model": "routed-deployment",
            "azure_model": identity,
            "reasoning_effort": effort,
            "scope": "conversation",
        },
    }
    entry = SimpleNamespace(
        entry_id="azure",
        title="Azure",
        data={"api_provider": "azure"},
        subentries={
            "assistant": SimpleNamespace(
                subentry_id="assistant",
                title="Assistant",
                subentry_type="conversation",
                data=options,
            )
        },
    )
    hass.config_entries.async_entries.return_value = [entry]
    monkeypatch.setattr(
        runtime,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(snapshot=lambda: {"rules": [rule]})),
    )
    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    manager.available_catalog = candidate
    return manager, options, rule, entry, candidate


@pytest.mark.parametrize(
    "identity,effort",
    [("o3-pro", "high"), ("o3-pro-2025-06-10", "high"), ("gpt-5.6", "max")],
)
@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_valid_bound_azure_route_survives_apply_and_reset(
    hass, monkeypatch, identity, effort, dynamic, restart
):
    manager, options, rule, entry, candidate = setup_manager(
        hass, monkeypatch, identity, effort
    )
    if dynamic:
        rule["action"]["model"] = "{deployment}"
    validate_rule_model_request(rule, options, entry.data)
    effective = {
        **options,
        "chat_model": "routed-deployment",
        "azure_model": identity,
        "reasoning_effort": effort,
    }
    before = build_provider_request_snapshot(
        effective, entry.data, tools_required=False
    ).api_kwargs
    if restart:
        manager.store.saved = {"catalog": candidate, "last_checked": 0}
        await manager.async_load()
        assert manager.status()["source"] == "downloaded"
    else:
        assert (await manager.async_apply_update())["source"] == "downloaded"
    assert (
        build_provider_request_snapshot(
            effective, entry.data, tools_required=False
        ).api_kwargs
        == before
    )
    assert (await manager.async_reset())["source"] == "bundled"
    assert (
        build_provider_request_snapshot(
            effective, entry.data, tools_required=False
        ).api_kwargs
        == before
    )


@pytest.mark.parametrize("dynamic", [False, True])
async def test_new_target_profile_cannot_invalidate_an_azure_route(
    hass, monkeypatch, dynamic
):
    identity = "new-underlying-model"
    manager, options, rule, entry, candidate = setup_manager(
        hass, monkeypatch, identity, None
    )
    if dynamic:
        rule["action"]["model"] = "{deployment}"
    validate_rule_model_request(rule, options, entry.data)
    model = deepcopy(
        next(item for item in candidate["models"] if item["id"] == "gpt-4.1-mini")
    )
    model.update(id=identity, display_name="New model", auto_api="chat_completions")
    model["recommended_profile"]["api"] = "chat_completions"
    model["api"]["responses"] = False
    model["function_calling"]["responses"] = False
    model["function_calling"]["preferred_api"] = "chat_completions"
    model["tools"]["function"]["responses"] = {"support": "never"}
    model["tools"]["web_search"]["responses"] = {"support": "never"}
    model["responses_web_search"] = False
    candidate["models"].append(model)
    data.validate_catalog(candidate)
    data.validate_catalog_transition(None, candidate)
    assert (await manager.async_apply_update())["source"] == "bundled"
    assert manager.catalog is None
    assert manager.available_catalog is candidate


@pytest.mark.parametrize(
    "identity", ["o3-pro", "o3-pro-2025-06-10", "gpt-5.6", "o3-pro-unclassified"]
)
@pytest.mark.parametrize("provider", [None, "openai", "azure"])
def test_prospective_metadata_matches_live_and_is_isolated(identity, provider):
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    original = deepcopy(candidate)
    prospective = data.catalog_model_metadata(candidate, identity, provider=provider)
    data.activate_catalog(candidate)
    assert prospective == data.model_metadata(identity, provider=provider)
    prospective["reasoning"]["efforts"].append("tampered")
    assert (
        "tampered"
        not in data.model_metadata(identity, provider=provider)["reasoning"]["efforts"]
    )
    assert candidate == original


async def test_inferred_azure_provider_uses_the_route_binding(hass, monkeypatch):
    manager, options, rule, entry, _candidate = setup_manager(
        hass, monkeypatch, "gpt-5.6", "max"
    )
    entry.data = {"base_url": "https://catalog-probe.openai.azure.com/openai/v1"}
    validate_rule_model_request(rule, options, entry.data)
    assert (await manager.async_apply_update())["source"] == "downloaded"
    assert (await manager.async_reset())["source"] == "bundled"


async def test_captured_underlying_model_remains_deferred(hass, monkeypatch):
    manager, options, rule, entry, _candidate = setup_manager(
        hass, monkeypatch, "{underlying}", "high"
    )
    validate_rule_model_request(rule, options, entry.data)
    assert (await manager.async_apply_update())["source"] == "downloaded"
    assert (await manager.async_reset())["source"] == "bundled"


@pytest.mark.parametrize("operation", ["apply", "reset"])
async def test_provider_projection_never_publishes_before_durable_save(
    hass, monkeypatch, operation
):
    manager, _options, _rule, _entry, candidate = setup_manager(
        hass, monkeypatch, "o3-pro", "high"
    )
    if operation == "reset":
        assert (await manager.async_apply_update())["source"] == "downloaded"
    before = data.model_metadata("o3-pro", provider="azure")
    generation = data.catalog_generation()
    manager.store.fail = True
    outcome = await (
        manager.async_apply_update() if operation == "apply" else manager.async_reset()
    )
    assert outcome["last_error"]
    assert outcome["source"] == ("bundled" if operation == "apply" else "downloaded")
    assert data.catalog_generation() == generation
    assert data.model_metadata("o3-pro", provider="azure") == before
    if operation == "apply":
        assert manager.available_catalog is candidate
    else:
        assert manager.catalog is candidate
