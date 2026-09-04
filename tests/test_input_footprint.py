"""Tests for content-free Usage input footprint telemetry."""

from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening,
)
from custom_components.extended_openai_conversation_responses.input_footprint import (
    _baseline_footprint,
    _latest_provider_usage,
    input_footprint_metrics,
)
from custom_components.extended_openai_conversation_responses.management_permissions import (
    wrap_management_permissions,
)


def test_live_footprint_reuses_context_serialization_without_changing_estimate() -> None:
    input_value = [
        {"role": "system", "content": "Use the kitchen light and remember café."},
        {"role": "user", "content": "Turn it on"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "light_on",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    metrics = input_footprint_metrics(input_value, tools)
    input_characters, _ = context_usage_hardening._serialized_characters(input_value)
    tool_characters, _ = context_usage_hardening._serialized_characters(tools)
    characters = input_characters + tool_characters

    assert metrics["input_characters"] == input_characters
    assert metrics["tool_characters"] == tool_characters
    assert metrics["characters"] == characters
    assert metrics["approx_tokens"] == (characters + 3) // 4
    assert metrics["context_safety_estimate_tokens"] == (
        context_usage_hardening.estimate_provider_input_tokens(input_value, tools)
    )


def test_baseline_reports_group_savings_and_approximate_tokens() -> None:
    result = _baseline_footprint(
        {
            "total_character_count": 1000,
            "function_group_savings": {"characters": 240, "percent": 19},
            "notes": ["Conversation history is excluded."],
        }
    )

    assert result["characters"] == 1000
    assert result["approx_tokens"] == 250
    assert result["without_function_groups_characters"] == 1240
    assert result["without_function_groups_approx_tokens"] == 310
    assert result["function_group_savings"] == {
        "characters": 240,
        "approx_tokens": 60,
        "percent": 19,
    }
    assert result["notes"] == ["Conversation history is excluded."]


def test_provider_usage_is_only_labeled_exact_when_input_tokens_exist() -> None:
    missing = SimpleNamespace(
        requests=[
            SimpleNamespace(
                timestamp="2026-09-04T12:00:00+00:00",
                input_tokens=0,
                cached_input_tokens=0,
                provider="openai",
                model="gpt-5.6",
                api_mode="responses",
            )
        ]
    )
    assert _latest_provider_usage(missing) is None

    reported = SimpleNamespace(
        requests=[
            SimpleNamespace(
                timestamp="2026-09-04T12:01:00+00:00",
                input_tokens=1234,
                cached_input_tokens=1000,
                provider="openai",
                model="gpt-5.6",
                api_mode="responses",
            )
        ]
    )
    assert _latest_provider_usage(reported) == {
        "timestamp": "2026-09-04T12:01:00+00:00",
        "input_tokens": 1234,
        "cached_input_tokens": 1000,
        "provider": "openai",
        "model": "gpt-5.6",
        "api_mode": "responses",
    }


@pytest.mark.asyncio
async def test_input_footprint_usage_action_remains_admin_only() -> None:
    async def original(*_args, **_kwargs):
        return {"ok": True}

    wrapped = wrap_management_permissions(original)
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await wrapped(
            None,  # type: ignore[arg-type]
            "normal-user",
            False,
            {"section": "usage", "action": "footprint"},
        )
