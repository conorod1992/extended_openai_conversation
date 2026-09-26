"""Exact current conversational model data and request behavior."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.model_capabilities import (
    ModelCapabilityError,
    get_model_capabilities,
    normalize_output_token_limit,
    parameter_is_allowed,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from custom_components.extended_openai_conversation_responses.prompt_cache import (
    _supports_explicit_cache,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-6-luna"])
def test_new_flagships_condition_chat_tools_on_none(model):
    capabilities = get_model_capabilities(model)
    assert capabilities["status"] == "current"
    assert capabilities["kind"] == "alias"
    assert capabilities["reasoning"] == {
        "supported": True,
        "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "by_api": {
            "responses": {"efforts": ["none", "low", "medium", "high", "xhigh", "max"]},
            "chat_completions": {"efforts": ["none", "low", "medium", "high", "xhigh"]},
        },
        "openai_default": "medium",
    }
    assert capabilities["limits"] == {
        "context_tokens": 1050000,
        "max_output_tokens": 128000,
    }
    assert capabilities["function_calling"]["chat_completions"] == {
        "support": "conditional",
        "allowed_reasoning_efforts": ["none"],
    }
    assert capabilities["responses_web_search"] is True
    assert capabilities["structured_outputs"] is True
    assert capabilities["explicit_prompt_cache"] is True
    assert capabilities["service_tiers"] == [
        "auto",
        "default",
        "flex",
        "fast",
        "priority",
    ]
    none = build_provider_request_snapshot(
        {
            "chat_model": model,
            "api_mode": "chat_completions",
            "reasoning_effort": "none",
        },
        {},
        tools_required=True,
    )
    assert none.api_mode == "chat_completions"
    with pytest.raises(HomeAssistantError, match="function/tool calling"):
        build_provider_request_snapshot(
            {
                "chat_model": model,
                "api_mode": "chat_completions",
                "reasoning_effort": "high",
            },
            {},
            tools_required=True,
        )
    auto = build_provider_request_snapshot(
        {"chat_model": model, "api_mode": "auto", "reasoning_effort": "high"},
        {},
        tools_required=True,
    )
    assert auto.api_mode == "responses"




@pytest.mark.parametrize(
    "model",
    ["gpt-6-sol", "gpt-6-luna", "gpt-5.5", "gpt-5.6", "gpt-5.6-sol"],
)
def test_verified_sampling_is_conditional_on_none(model):
    capabilities = get_model_capabilities(model)
    expected = {
        "support": "conditional",
        "allowed_reasoning_efforts": ["none"],
        "send_policy": "omit_unless_configured",
    }
    assert capabilities["temperature"] == expected
    assert capabilities["top_p"] == expected
    assert parameter_is_allowed(model, "temperature", "none") is True
    assert parameter_is_allowed(model, "top_p", "none") is True
    assert parameter_is_allowed(model, "temperature", "low") is False
    assert parameter_is_allowed(model, "top_p", "low") is False

    none = build_provider_request_snapshot(
        {
            "chat_model": model,
            "api_mode": "responses",
            "reasoning_effort": "none",
            "temperature": 0.7,
            "top_p": 0.7,
        },
        {},
    )
    assert none.api_kwargs["temperature"] == 0.7
    assert none.api_kwargs["top_p"] == 0.7

    low = build_provider_request_snapshot(
        {
            "chat_model": model,
            "api_mode": "responses",
            "reasoning_effort": "low",
            "temperature": 0.7,
            "top_p": 0.7,
        },
        {},
    )
    assert "temperature" not in low.api_kwargs
    assert "top_p" not in low.api_kwargs


@pytest.mark.parametrize("model", ["gpt-5.6-terra", "gpt-5.6-luna"])
def test_unverified_56_variants_keep_sampling_undocumented(model):
    capabilities = get_model_capabilities(model)
    for parameter in ("temperature", "top_p"):
        assert capabilities[parameter] == {
            "support": "undocumented",
            "allowed_reasoning_efforts": None,
            "send_policy": "omit",
        }


def test_cache_and_flex_corrections():
    assert _supports_explicit_cache("gpt-6-astra") is True
    for model in ("gpt-5.5", "gpt-5.4"):
        caps = get_model_capabilities(model)
        assert caps["service_tiers"] == ["auto", "default", "flex"]
        request = build_provider_request_snapshot(
            {"chat_model": model, "service_tier": "flex"}, {}
        )
        assert request.api_kwargs["service_tier"] == "flex"


@pytest.mark.parametrize(
    (
        "model",
        "efforts",
        "provider_default",
        "context",
        "output",
        "stream",
        "structured",
    ),
    [
        (
            "gpt-5.5-pro",
            ["medium", "high", "xhigh"],
            "high",
            1050000,
            128000,
            False,
            True,
        ),
        (
            "gpt-5.4-pro",
            ["medium", "high", "xhigh"],
            "medium",
            1050000,
            128000,
            True,
            False,
        ),
        ("gpt-5.2-pro", ["medium", "high", "xhigh"], None, 400000, 128000, True, False),
        ("gpt-5-pro", ["high"], "high", 400000, 272000, True, True),
        ("o3-pro", [], None, 200000, 100000, False, False),
    ],
)
def test_pro_records_and_exact_output_ceiling(
    model, efforts, provider_default, context, output, stream, structured
):
    caps = get_model_capabilities(model)
    assert caps["status"] == "current"
    assert caps["kind"] == "alias"
    assert caps["api"]["responses"] is True
    assert caps["api"]["chat_completions"] is False
    assert caps["function_calling"]["responses"] is True
    assert caps["function_calling"]["chat_completions"] is False
    assert caps["reasoning"]["efforts"] == efforts
    assert caps["reasoning"]["openai_default"] == provider_default
    assert caps["limits"] == {"context_tokens": context, "max_output_tokens": output}
    assert caps["streaming"] is stream
    assert caps["structured_outputs"] is structured
    assert caps["explicit_prompt_cache"] is False
    assert normalize_output_token_limit(model, "responses", output) == (
        "max_output_tokens",
        output,
    )
    with pytest.raises(ModelCapabilityError, match="exceeds"):
        normalize_output_token_limit(model, "responses", output + 1)
    snapshot = build_provider_request_snapshot(
        {"chat_model": model, "api_mode": "auto", "max_tokens": output}, {}
    )
    assert snapshot.api_kwargs["stream"] is stream
    assert snapshot.structured_outputs is structured


def test_catalog_scope_excludes_unverified_aliases():
    ids = {model["id"] for model in BUNDLED_CATALOG["models"]}
    assert "gpt-5.6-pro" not in ids
