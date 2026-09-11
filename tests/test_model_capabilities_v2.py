"""Model capability v2 validation and production request-shape tests."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import model_catalog
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    ModelCapabilityError,
    get_model_capabilities,
    normalize_output_token_limit,
    parameter_is_allowed,
    recommended_reasoning_effort,
    select_api_path,
    validate_api_path,
    validate_reasoning_effort,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


def snapshot(model: str, *, api="responses", tools=False, **values):
    options = {"chat_model": model, "api_mode": api, "max_tokens": 1000, **values}
    return build_provider_request_snapshot(options, {}, tools_required=tools)


# A. Astra reasoning enum.
def test_astra_reasoning_enum():
    with pytest.raises(ModelCapabilityError):
        validate_reasoning_effort("gpt-6-astra", "none")
    for effort in ("low", "medium", "high", "xhigh", "max"):
        assert validate_reasoning_effort("gpt-6-astra", effort) == effort


# B/C. Astra API-path behavior.
def test_astra_chat_without_tools_is_valid():
    assert validate_api_path("gpt-6-astra", "chat_completions", False) == "chat_completions"
    result = snapshot("gpt-6-astra", api="chat_completions", tools=False, reasoning_effort="low")
    assert result.api_mode == "chat_completions"
    assert result.api_kwargs["max_completion_tokens"] == 1000
    assert "max_output_tokens" not in result.api_kwargs


def test_astra_chat_with_tools_forced_fails_and_auto_uses_responses():
    with pytest.raises(ModelCapabilityError):
        validate_api_path("gpt-6-astra", "chat_completions", True)
    with pytest.raises(HomeAssistantError):
        snapshot("gpt-6-astra", api="chat_completions", tools=True, reasoning_effort="low")

    result = snapshot(
        "gpt-6-astra",
        api="auto",
        tools=True,
        reasoning_effort="low",
        temperature=0.2,
        top_p=0.8,
    )
    assert result.api_mode == "responses"
    assert result.api_kwargs["reasoning"] == {"effort": "low"}
    assert result.api_kwargs["max_output_tokens"] == 1000
    assert "temperature" not in result.api_kwargs
    assert "top_p" not in result.api_kwargs
    assert "max_tokens" not in result.api_kwargs


# D/E. GPT-5.6 exact reasoning enum and undocumented sampling.
def test_gpt_56_reasoning_and_defaults():
    expected = ["none", "low", "medium", "high", "xhigh", "max"]
    caps = get_model_capabilities("gpt-5.6")
    assert caps["reasoning"]["efforts"] == expected
    assert caps["reasoning"]["openai_default"] == "medium"
    assert recommended_reasoning_effort("gpt-5.6") == "low"
    for effort in expected:
        assert validate_reasoning_effort("gpt-5.6", effort) == effort
    with pytest.raises(ModelCapabilityError):
        validate_reasoning_effort("gpt-5.6", "ultra")

    alias = get_model_capabilities("gpt-5.6")
    sol = get_model_capabilities("gpt-5.6-sol")
    assert alias["alias_of"] == "gpt-5.6-sol"
    for key in (
        "api",
        "function_calling",
        "reasoning",
        "temperature",
        "top_p",
        "limits",
        "streaming",
        "output_tokens",
        "recommended_profile",
    ):
        assert alias[key] == sol[key]


def test_gpt_56_undocumented_sampling_is_omitted():
    result = snapshot(
        "gpt-5.6-terra",
        reasoning_effort="low",
        temperature=0.2,
        top_p=0.8,
        tools=True,
    )
    assert result.api_mode == "responses"
    assert result.api_kwargs["reasoning"] == {"effort": "low"}
    assert "temperature" not in result.api_kwargs
    assert "top_p" not in result.api_kwargs


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_gpt_54_sampling_only_allowed_at_none(parameter, effort):
    assert parameter_is_allowed("gpt-5.4", parameter, "none") is True
    assert parameter_is_allowed("gpt-5.4", parameter, effort) is False
    allowed = snapshot("gpt-5.4", reasoning_effort="none", **{parameter: 0.2})
    assert allowed.api_kwargs[parameter] == 0.2
    migrated = snapshot("gpt-5.4", reasoning_effort=effort, **{parameter: 0.2})
    assert parameter not in migrated.api_kwargs


# H. GPT-5.2 sampling.
def test_gpt_52_sampling_rules():
    assert snapshot("gpt-5.2", reasoning_effort="none", temperature=0.2).api_kwargs["temperature"] == 0.2
    assert "temperature" not in snapshot("gpt-5.2", reasoning_effort="medium", temperature=0.2).api_kwargs
    assert snapshot("gpt-5.2", reasoning_effort="none", top_p=0.7).api_kwargs["top_p"] == 0.7
    assert "top_p" not in snapshot("gpt-5.2", reasoning_effort="xhigh", top_p=0.7).api_kwargs


# I. GPT-5.1 sampling.
def test_gpt_51_sampling_rules():
    allowed = snapshot("gpt-5.1", reasoning_effort="none", temperature=0.2, top_p=0.7)
    assert allowed.api_kwargs["temperature"] == 0.2
    assert allowed.api_kwargs["top_p"] == 0.7
    for effort in ("low", "medium", "high"):
        result = snapshot("gpt-5.1", reasoning_effort=effort, temperature=0.2, top_p=0.7)
        assert "temperature" not in result.api_kwargs
        assert "top_p" not in result.api_kwargs


# J. Initial GPT-5 family.
@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5-nano"])
def test_initial_gpt5_efforts_and_sampling(model):
    caps = get_model_capabilities(model)
    assert caps["reasoning"]["efforts"] == ["minimal", "low", "medium", "high"]
    for effort in caps["reasoning"]["efforts"]:
        result = snapshot(model, reasoning_effort=effort, temperature=0.2, top_p=0.7)
        assert "temperature" not in result.api_kwargs
        assert "top_p" not in result.api_kwargs


# K. Non-reasoning GPT models.
@pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"])
def test_non_reasoning_models_sampling_and_reasoning_validation(model):
    caps = get_model_capabilities(model)
    assert caps["reasoning"] == {"supported": False, "efforts": [], "openai_default": None}
    assert parameter_is_allowed(model, "temperature", None)
    assert parameter_is_allowed(model, "top_p", None)
    with pytest.raises(ModelCapabilityError):
        validate_reasoning_effort(model, "low")
    result = snapshot(model, temperature=0.2, top_p=0.7)
    assert "reasoning" not in result.api_kwargs
    assert "reasoning_effort" not in result.api_kwargs
    assert result.api_kwargs["temperature"] == 0.2
    assert result.api_kwargs["top_p"] == 0.7


# L. o3.
def test_o3_capabilities_and_payload():
    for effort in ("low", "medium", "high"):
        assert validate_reasoning_effort("o3", effort) == effort
    for effort in ("none", "minimal", "xhigh", "max"):
        with pytest.raises(ModelCapabilityError):
            validate_reasoning_effort("o3", effort)
    result = snapshot("o3", reasoning_effort="high", temperature=0.2, top_p=0.7)
    assert result.api_kwargs["reasoning"] == {"effort": "high"}
    assert "temperature" not in result.api_kwargs
    assert "top_p" not in result.api_kwargs
    assert "max_tokens" not in result.api_kwargs


# M/N. Modern output-token parameters and old logical max_tokens migration.
def test_output_token_parameter_mapping_and_legacy_value():
    responses = snapshot("gpt-5.4", api="responses", reasoning_effort="none")
    chat = snapshot("gpt-5.4", api="chat_completions", reasoning_effort="none")
    assert responses.api_kwargs["max_output_tokens"] == 1000
    assert chat.api_kwargs["max_completion_tokens"] == 1000
    assert "max_tokens" not in responses.api_kwargs
    assert "max_tokens" not in chat.api_kwargs

    old_responses = build_provider_request_snapshot(
        {"chat_model": "gpt-5.4", "api_mode": "responses", "reasoning_effort": "none", "max_tokens": 750},
        {},
        tools_required=False,
    )
    old_chat = build_provider_request_snapshot(
        {"chat_model": "gpt-5.4", "api_mode": "chat_completions", "reasoning_effort": "none", "max_tokens": 750},
        {},
        tools_required=False,
    )
    assert old_responses.api_kwargs["max_output_tokens"] == 750
    assert old_chat.api_kwargs["max_completion_tokens"] == 750
    assert "max_tokens" not in old_responses.api_kwargs
    assert "max_tokens" not in old_chat.api_kwargs


# O. Output ceilings.
@pytest.mark.parametrize(
    ("model", "ceiling"),
    [("gpt-4o", 16384), ("gpt-4.1", 32768), ("gpt-5.6-terra", 128000), ("o3", 100000)],
)
def test_output_ceiling_validation(model, ceiling):
    assert normalize_output_token_limit(model, "responses", ceiling) == ("max_output_tokens", ceiling)
    with pytest.raises(ModelCapabilityError):
        normalize_output_token_limit(model, "responses", ceiling + 1)


# P/Q/R. Lifecycle is exact-ID catalog data, not age or name inference.
def test_deprecated_picker_and_stable_alias_status_are_exact():
    normal_ids = {item["id"] for item in model_catalog.catalog_picker_models()}
    assert "gpt-4-turbo" not in normal_ids
    assert "o2" not in normal_ids
    assert "o4" not in normal_ids
    assert "gpt-5.3" not in normal_ids
    existing = {
        item["id"]: item
        for item in model_catalog.catalog_picker_models(selected_model="gpt-4-turbo")
    }
    assert existing["gpt-4-turbo"]["status"] == "deprecated"
    assert model_catalog.model_metadata("gpt-5.6")["status"] == "current"


# S. Unknown/custom models remain loadable but conservative.
def test_unknown_custom_model_is_conservative_and_does_not_crash_request_builder():
    model = "my-future-openai-model"
    caps = get_model_capabilities(model)
    assert caps["id"] == model
    assert caps["status"] == "unknown"
    assert caps["function_calling"]["responses"] is False
    assert caps["function_calling"]["chat_completions"] is False
    result = snapshot(model, tools=False, temperature=0.2, top_p=0.7)
    assert result.api_kwargs["model"] == model
    assert "temperature" not in result.api_kwargs
    assert "top_p" not in result.api_kwargs


# T. All current catalog models stream.
@pytest.mark.parametrize(
    "model",
    [item["id"] for item in model_catalog.BUNDLED_CATALOG["models"] if item["status"] == "current"],
)
def test_every_current_model_streaming(model):
    assert get_model_capabilities(model)["streaming"] is True


# Required production request-shape spot checks.
def test_request_shape_gpt54_none_temperature():
    result = snapshot("gpt-5.4", reasoning_effort="none", temperature=0.2)
    assert result.api_kwargs["reasoning"] == {"effort": "none"}
    assert result.api_kwargs["temperature"] == 0.2


def test_request_shape_gpt54_high_migrated_temperature_omitted():
    result = snapshot("gpt-5.4", reasoning_effort="high", temperature=0.2)
    assert result.api_kwargs["reasoning"] == {"effort": "high"}
    assert "temperature" not in result.api_kwargs


def test_request_shape_gpt52_none_top_p():
    result = snapshot("gpt-5.2", reasoning_effort="none", top_p=0.6)
    assert result.api_kwargs["top_p"] == 0.6


def test_request_shape_gpt5_minimal():
    result = snapshot("gpt-5", reasoning_effort="minimal", temperature=0.2, top_p=0.6)
    assert result.api_kwargs["reasoning"] == {"effort": "minimal"}
    assert "temperature" not in result.api_kwargs
    assert "top_p" not in result.api_kwargs


def test_request_shape_gpt41_has_no_reasoning_and_keeps_valid_temperature():
    result = snapshot("gpt-4.1", temperature=0.2)
    assert result.api_kwargs["temperature"] == 0.2
    assert "reasoning" not in result.api_kwargs
    assert "reasoning_effort" not in result.api_kwargs


def test_request_shape_o3_token_field_by_api():
    responses = snapshot("o3", api="responses", reasoning_effort="medium")
    chat = snapshot("o3", api="chat_completions", reasoning_effort="medium")
    assert responses.api_kwargs["max_output_tokens"] == 1000
    assert responses.api_kwargs["reasoning"] == {"effort": "medium"}
    assert chat.api_kwargs["max_completion_tokens"] == 1000
    assert chat.api_kwargs["reasoning_effort"] == "medium"
    assert "max_tokens" not in responses.api_kwargs
    assert "max_tokens" not in chat.api_kwargs


def test_select_api_path_prefers_responses_for_astra_tools():
    assert select_api_path("gpt-6-astra", "auto", True) == "responses"
