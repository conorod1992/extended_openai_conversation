"""Focused tests for model capability validation and request normalization."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import model_capabilities
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_AUTO,
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    ModelCapabilityError,
)


def _capabilities() -> dict[str, Any]:
    """Return a small internally consistent capability record for branch tests."""
    return {
        "status": "known",
        "reasoning": {
            "supported": True,
            "efforts": ["low", "high"],
        },
        "temperature": {
            "support": "conditional",
            "allowed_reasoning_efforts": ["low"],
        },
        "top_p": {
            "support": "never",
            "allowed_reasoning_efforts": None,
        },
        "api": {
            API_MODE_RESPONSES: True,
            API_MODE_CHAT_COMPLETIONS: True,
        },
        "function_calling": {
            "preferred_api": API_MODE_RESPONSES,
            API_MODE_RESPONSES: True,
            API_MODE_CHAT_COMPLETIONS: True,
        },
        "auto_api": API_MODE_RESPONSES,
        "recommended_profile": {"reasoning_effort": "low"},
        "limits": {"max_output_tokens": 100},
        "output_tokens": {
            API_MODE_RESPONSES: "max_output_tokens",
            API_MODE_CHAT_COMPLETIONS: "max_completion_tokens",
        },
    }


def _install_capabilities(
    monkeypatch: pytest.MonkeyPatch, capabilities: dict[str, Any]
) -> None:
    """Make one synthetic capability record authoritative for the test."""
    monkeypatch.setattr(
        model_capabilities,
        "get_model_capabilities",
        lambda _model: capabilities,
    )


def test_reasoning_validation_rejects_settings_for_unsupported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported models accept omission but reject an explicit effort."""
    capabilities = _capabilities()
    capabilities["reasoning"] = {"supported": False, "efforts": []}
    _install_capabilities(monkeypatch, capabilities)

    assert model_capabilities.validate_reasoning_effort("model", None) is None
    with pytest.raises(ModelCapabilityError, match="does not support reasoning_effort"):
        model_capabilities.validate_reasoning_effort("model", "low")


def test_reasoning_validation_rejects_unknown_supported_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reasoning-capable model still rejects values outside its exact enum."""
    _install_capabilities(monkeypatch, _capabilities())

    with pytest.raises(ModelCapabilityError, match="allowed: low, high"):
        model_capabilities.validate_reasoning_effort("model", "medium")


def test_sampling_parameter_support_is_exact_and_effort_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conditional sampling is allowed only for the catalogued reasoning effort."""
    capabilities = _capabilities()
    _install_capabilities(monkeypatch, capabilities)

    assert model_capabilities.parameter_is_allowed("model", "temperature", "low")
    assert not model_capabilities.parameter_is_allowed("model", "temperature", "high")
    assert not model_capabilities.parameter_is_allowed("model", "top_p", "low")

    capabilities["temperature"]["support"] = "always"
    assert model_capabilities.parameter_is_allowed("model", "temperature", "high")

    with pytest.raises(ModelCapabilityError, match="Unknown sampling parameter"):
        model_capabilities.parameter_is_allowed("model", "frequency_penalty", None)


def test_validate_api_path_rejects_unknown_unsupported_and_tool_incompatible_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit paths fail clearly when the API or its tool support is unavailable."""
    capabilities = _capabilities()
    _install_capabilities(monkeypatch, capabilities)

    with pytest.raises(ModelCapabilityError, match="Unknown API path"):
        model_capabilities.validate_api_path("model", API_MODE_AUTO)

    capabilities["api"][API_MODE_CHAT_COMPLETIONS] = False
    with pytest.raises(ModelCapabilityError, match="does not support chat_completions"):
        model_capabilities.validate_api_path("model", API_MODE_CHAT_COMPLETIONS)

    capabilities["api"][API_MODE_CHAT_COMPLETIONS] = True
    capabilities["function_calling"][API_MODE_CHAT_COMPLETIONS] = False
    with pytest.raises(ModelCapabilityError, match="function/tool calling"):
        model_capabilities.validate_api_path(
            "model", API_MODE_CHAT_COMPLETIONS, tools_required=True
        )


def test_auto_tool_path_falls_back_from_unusable_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto tool routing chooses another valid API when the preference cannot serve tools."""
    capabilities = _capabilities()
    capabilities["function_calling"][API_MODE_RESPONSES] = False
    _install_capabilities(monkeypatch, capabilities)

    assert (
        model_capabilities.select_api_path("model", API_MODE_AUTO, tools_required=True)
        == API_MODE_CHAT_COMPLETIONS
    )


def test_auto_tool_path_rejects_model_without_tool_capable_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto routing fails rather than silently selecting an API that cannot call tools."""
    capabilities = _capabilities()
    capabilities["function_calling"][API_MODE_RESPONSES] = False
    capabilities["function_calling"][API_MODE_CHAT_COMPLETIONS] = False
    _install_capabilities(monkeypatch, capabilities)

    with pytest.raises(ModelCapabilityError, match="no supported API path"):
        model_capabilities.select_api_path("model", API_MODE_AUTO, tools_required=True)


def test_auto_conversation_path_falls_back_and_rejects_no_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad preferred path falls back, while a model with no API fails closed."""
    capabilities = _capabilities()
    capabilities["auto_api"] = API_MODE_RESPONSES
    capabilities["api"][API_MODE_RESPONSES] = False
    _install_capabilities(monkeypatch, capabilities)

    assert (
        model_capabilities.select_api_path("model", API_MODE_AUTO)
        == API_MODE_CHAT_COMPLETIONS
    )

    capabilities["api"][API_MODE_CHAT_COMPLETIONS] = False
    with pytest.raises(ModelCapabilityError, match="no supported conversational API path"):
        model_capabilities.select_api_path("model", API_MODE_AUTO)


def test_unknown_model_auto_path_remains_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown/custom models prefer Responses instead of inheriting known-model defaults."""
    capabilities = _capabilities()
    capabilities["status"] = "unknown"
    capabilities["auto_api"] = API_MODE_CHAT_COMPLETIONS
    _install_capabilities(monkeypatch, capabilities)

    assert model_capabilities.select_api_path("custom", API_MODE_AUTO) == API_MODE_RESPONSES


def test_output_token_limit_rejects_invalid_values_and_model_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured output limits must be positive integers within the model ceiling."""
    _install_capabilities(monkeypatch, _capabilities())

    assert (
        model_capabilities.normalize_output_token_limit(
            "model", API_MODE_RESPONSES, None
        )
        is None
    )
    assert (
        model_capabilities.normalize_output_token_limit("model", API_MODE_RESPONSES, "")
        is None
    )

    with pytest.raises(ModelCapabilityError, match="must be an integer"):
        model_capabilities.normalize_output_token_limit(
            "model", API_MODE_RESPONSES, "many"
        )
    with pytest.raises(ModelCapabilityError, match="greater than zero"):
        model_capabilities.normalize_output_token_limit("model", API_MODE_RESPONSES, 0)
    with pytest.raises(ModelCapabilityError, match="maximum of 100"):
        model_capabilities.normalize_output_token_limit("model", API_MODE_RESPONSES, 101)


def test_output_token_limit_uses_modern_field_and_rejects_legacy_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalization emits the path-specific modern field and never max_tokens."""
    capabilities = _capabilities()
    _install_capabilities(monkeypatch, capabilities)

    assert model_capabilities.normalize_output_token_limit(
        "model", API_MODE_RESPONSES, "42"
    ) == ("max_output_tokens", 42)

    capabilities["output_tokens"][API_MODE_RESPONSES] = "max_tokens"
    with pytest.raises(ModelCapabilityError, match="Legacy max_tokens"):
        model_capabilities.normalize_output_token_limit("model", API_MODE_RESPONSES, 42)


def test_sampling_configuration_omits_legacy_defaults_and_rejects_unknown_parameter() -> None:
    """Migration defaults remain omitted while explicit non-default values are retained."""
    assert not model_capabilities.sampling_value_is_configured("temperature", None, 1.0)
    assert not model_capabilities.sampling_value_is_configured("temperature", 1.0, 1.0)
    assert model_capabilities.sampling_value_is_configured("temperature", 0.0, 1.0)

    with pytest.raises(ModelCapabilityError, match="Unknown sampling parameter"):
        model_capabilities.sampling_value_is_configured("frequency_penalty", 0.5, 0.0)
