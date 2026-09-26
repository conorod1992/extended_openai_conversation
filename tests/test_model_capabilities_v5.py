"""Provider-discovered API and tool conditions remain catalog driven."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import model_catalog
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    ModelCapabilityError,
    capability_allowed,
    frontend_capabilities,
    select_api_path,
    validate_api_path,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    "model", ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
)
def test_gpt_56_api_effort_and_functions(model):
    assert validate_api_path(model, "responses", True, "max") == "responses"
    with pytest.raises(ModelCapabilityError, match="reasoning_effort"):
        validate_api_path(model, "chat_completions", False, "max")
    assert (
        validate_api_path(model, "chat_completions", False, "high")
        == "chat_completions"
    )
    with pytest.raises(ModelCapabilityError, match="function/tool calling"):
        validate_api_path(model, "chat_completions", True, "high")
    assert (
        validate_api_path(model, "chat_completions", True, "none") == "chat_completions"
    )
    assert validate_api_path(model, "responses", True, "high") == "responses"
    assert select_api_path(model, "auto", True, "high") == "responses"
    assert select_api_path(model, "auto", False, "max") == "responses"


def test_mini_web_search_condition_and_auto():
    assert not capability_allowed(
        "gpt-5-mini", "web_search", "responses", effort="minimal"
    )
    for effort in ("low", "medium", "high"):
        assert capability_allowed(
            "gpt-5-mini", "web_search", "responses", effort=effort
        )
        assert (
            select_api_path("gpt-5-mini", "auto", effort=effort, web_search=True)
            == "responses"
        )
    with pytest.raises(ModelCapabilityError, match="Web Search"):
        validate_api_path("gpt-5-mini", "responses", effort="minimal", web_search=True)


def test_request_snapshot_rejects_invalid_choices_before_provider_call():
    base = {"chat_model": "gpt-5.6", "api_mode": "chat_completions", "max_tokens": 1000}
    with pytest.raises(HomeAssistantError, match="reasoning_effort"):
        build_provider_request_snapshot(
            {**base, "reasoning_effort": "max"}, {}, tools_required=False
        )
    plain = build_provider_request_snapshot(
        {**base, "reasoning_effort": "high"}, {}, tools_required=False
    )
    assert plain.api_kwargs["reasoning_effort"] == "high"
    with pytest.raises(HomeAssistantError, match="function/tool calling"):
        build_provider_request_snapshot(
            {**base, "reasoning_effort": "high"}, {}, tools_required=True
        )
    allowed = build_provider_request_snapshot(
        {**base, "reasoning_effort": "none"}, {}, tools_required=True
    )
    assert allowed.api_mode == "chat_completions"
    responses = build_provider_request_snapshot(
        {**base, "api_mode": "responses", "reasoning_effort": "max"},
        {},
        tools_required=True,
    )
    assert responses.api_kwargs["reasoning"] == {"effort": "max"}


def test_web_search_request_snapshot_condition():
    base = {"chat_model": "gpt-5-mini", "api_mode": "responses", "web_search": True}
    with pytest.raises(HomeAssistantError, match="Web Search"):
        build_provider_request_snapshot(
            {**base, "reasoning_effort": "minimal"}, {"api_provider": "openai"}
        )
    allowed = build_provider_request_snapshot(
        {**base, "reasoning_effort": "low"}, {"api_provider": "openai"}
    )
    assert allowed.provider_tools[0]["type"] == "web_search"


def test_ui_projection_uses_backend_evaluation():
    capabilities = frontend_capabilities("gpt-5.6")
    assert (
        "max" not in capabilities["reasoning"]["by_api"]["chat_completions"]["efforts"]
    )
    assert capabilities["evaluations"]["responses"]["max"]["function"]
    assert not capabilities["evaluations"]["chat_completions"]["high"]["function"]
    assert (
        frontend_capabilities("gpt-5-mini")["evaluations"]["responses"]["minimal"][
            "web_search"
        ]
        is False
    )
    nonreasoning = frontend_capabilities("gpt-4.1")
    assert nonreasoning["evaluations"]["responses"]["null"]["web_search"]
    assert nonreasoning["auto_paths"]["null:0:1"] == "responses"


def test_v4_catalog_migrates_boolean_tools():
    old = deepcopy(model_catalog.BUNDLED_CATALOG)
    old["schema_version"] = 4
    old["catalog_version"] = 6
    for item in (old["defaults"], *old["models"]):
        item.pop("tools", None)
        if "reasoning" in item:
            item["reasoning"].pop("by_api", None)
    previous = next(item for item in old["models"] if item["id"] == "gpt-5.6")
    previous["function_calling"]["chat_completions"] = True
    migrated, changed = model_catalog.validate_or_migrate_catalog(old)
    assert changed and migrated["schema_version"] == 5
    model = next(item for item in migrated["models"] if item["id"] == "gpt-5.6")
    assert model["tools"]["function"]["chat_completions"]["support"] == "always"
    assert "max" in model["reasoning"]["by_api"]["chat_completions"]["efforts"]
    model_catalog.validate_catalog_transition(migrated, model_catalog.BUNDLED_CATALOG)


def test_catalog_rejects_unknown_condition_dimension():
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5-mini")
    model["tools"]["web_search"]["responses"]["requires"] = {"location": ["world"]}
    with pytest.raises(ValueError, match="Invalid tool condition"):
        model_catalog.validate_catalog(candidate)
