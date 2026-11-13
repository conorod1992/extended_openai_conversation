"""Creation defaults cannot migrate legacy or explicit model selections."""

from datetime import date

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.config_flow import (
    DEFAULT_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.model_lifecycle import (
    lifecycle_snapshot,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("options", [DEFAULT_OPTIONS, DEFAULT_AI_TASK_OPTIONS])
@pytest.mark.parametrize("mode", ["auto", "responses", "chat_completions"])
def test_fresh_assistant_defaults_support_tools_on_each_configured_api(options, mode):
    snapshot = build_provider_request_snapshot(
        {**options, "api_mode": mode}, {}, tools_required=True
    )
    assert options["chat_model"] == DEFAULT_CHAT_MODEL
    assert snapshot.api_kwargs["model"] == DEFAULT_CHAT_MODEL
    token_key = (
        "max_output_tokens"
        if snapshot.api_mode == "responses"
        else "max_completion_tokens"
    )
    assert snapshot.api_kwargs[token_key] == 500
    assert snapshot.api_kwargs["stream"] is True
    assert snapshot.api_kwargs.get("store", False) is False
    assert snapshot.structured_outputs is True


@pytest.mark.parametrize(
    "mode,effort", [("responses", "low"), ("chat_completions", "none")]
)
def test_explicit_luna_profile_supports_tools_without_rewriting_selection(mode, effort):
    options = {"chat_model": "gpt-6-luna", "api_mode": mode, "reasoning_effort": effort}
    before = dict(options)
    snapshot = build_provider_request_snapshot(options, {}, tools_required=True)
    assert snapshot.api_mode == mode and snapshot.api_kwargs["model"] == "gpt-6-luna"
    assert options == before


def test_explicit_incompatible_luna_profile_is_not_silently_rewritten():
    options = {
        "chat_model": "gpt-6-luna",
        "api_mode": "chat_completions",
        "reasoning_effort": "low",
    }
    before = dict(options)
    with pytest.raises(
        HomeAssistantError, match="does not support function/tool calling"
    ):
        build_provider_request_snapshot(options, {}, tools_required=True)
    assert options == before


def test_missing_model_preserves_legacy_interpretation_and_explicit_values(hass):
    assert DEFAULT_CHAT_MODEL == "gpt-5-mini"
    assert agent_config_defaults()["chat_model"] == DEFAULT_CHAT_MODEL
    assert normalize_agent_config({})["chat_model"] == DEFAULT_CHAT_MODEL
    assert (
        build_provider_request_snapshot({}, {}).api_kwargs["model"]
        == DEFAULT_CHAT_MODEL
    )
    assert DEFAULT_AI_TASK_OPTIONS["chat_model"] == DEFAULT_CHAT_MODEL
    assert "reasoning_effort" not in DEFAULT_AI_TASK_OPTIONS
    for model in ["gpt-5-mini", "custom-model"]:
        options = {"chat_model": model, "max_tokens": 1200, "service_tier": "default"}
        assert normalize_agent_config(options)["chat_model"] == model
        snapshot = build_provider_request_snapshot(
            options, {"base_url": "https://example.test/v1"}
        )
        assert snapshot.api_kwargs["model"] == model
        assert (
            next(
                snapshot.api_kwargs[key]
                for key in ("max_output_tokens", "max_completion_tokens")
                if key in snapshot.api_kwargs
            )
            == 1200
        )
    snapshot = build_provider_request_snapshot(
        {"chat_model": "deployment", "azure_model": "gpt-5-mini", "max_tokens": 700},
        {"api_provider": "azure"},
    )
    assert snapshot.api_kwargs["model"] == "deployment"
    assert (
        snapshot.api_kwargs.get(
            "max_output_tokens", snapshot.api_kwargs.get("max_completion_tokens")
        )
        == 700
    )


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-pro",
        "o3",
        "o3-pro",
        "o3-pro-2025-06-10",
    ],
)
def test_december_lifecycle_inherited_by_recognized_snapshots(model):
    before = lifecycle_snapshot(model, today=date(2026, 12, 10))
    after = lifecycle_snapshot(model, today=date(2026, 12, 11))
    assert before["status"] == "deprecated"
    assert before["deprecated_at"] == "2026-06-11"
    assert before["shutdown_at"] == "2026-12-11"
    assert not before["shutdown_reached"]
    assert after["shutdown_reached"]


def test_turbo_shutdown_is_recorded():
    snapshot = lifecycle_snapshot("gpt-4-turbo", today=date(2026, 11, 12))
    assert snapshot["shutdown_at"] == "2026-10-23"
    assert snapshot["shutdown_reached"]
