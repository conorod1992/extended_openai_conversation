"""Tests for metadata-only payload and latency diagnostics."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.payload_diagnostics import (
    APPROX_TOKEN_METHOD,
    cache_usage_metrics,
    largest_contributors,
    prompt_metrics,
    provider_payload_metrics,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    EffectivePrompt,
    PromptSection,
)


def test_prompt_metrics_preserve_exact_section_sizes_and_stable_prefix() -> None:
    effective = EffectivePrompt(
        "stable one\nstable two\nuser value\nvolatile",
        (
            PromptSection("one", "Stable one", "stable one", "stable"),
            PromptSection("two", "Stable two", "stable two", "stable"),
            PromptSection("user_prompt", "Rendered user prompt", "user value", "mixed"),
            PromptSection("clock", "Clock", "volatile", "volatile"),
        ),
    )

    metrics = prompt_metrics(effective)

    assert metrics["characters"] == len(effective.text)
    assert metrics["approximation_method"] == APPROX_TOKEN_METHOD
    assert metrics["sections"][0] == {
        "key": "one",
        "label": "Stable one",
        "volatility": "stable",
        "characters": len("stable one"),
        "approx_tokens": 3,
    }
    assert metrics["integration_stable_prefix"] == {
        "section_count": 2,
        "characters": len("stable one\nstable two"),
        "approx_tokens": 6,
        "first_non_stable_section": "user_prompt",
    }
    assert metrics["section_characters_by_volatility"] == {
        "stable": len("stable one") + len("stable two"),
        "mixed": len("user value"),
        "volatile": len("volatile"),
    }


def test_provider_payload_metrics_are_content_free_and_break_down_tool_results() -> None:
    provider_input = [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": "secret stable prefix",
                    "prompt_cache_breakpoint": {"mode": "explicit"},
                },
                {"type": "input_text", "text": "secret volatile suffix"},
            ],
        },
        {"role": "user", "content": "private user question"},
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "private result",
        },
    ]
    tools = [
        {
            "type": "function",
            "name": "small_tool",
            "description": "private description",
            "parameters": {"type": "object"},
        },
        {
            "type": "function",
            "name": "large_tool",
            "description": "x" * 200,
            "parameters": {"type": "object"},
        },
    ]

    metrics = provider_payload_metrics(provider_input, tools)

    assert metrics["approximation_method"] == APPROX_TOKEN_METHOD
    assert metrics["input_breakdown"]["by_kind"]["system"]["count"] == 1
    assert metrics["input_breakdown"]["by_kind"]["user"]["count"] == 1
    assert metrics["input_breakdown"]["by_kind"]["tool_result"]["count"] == 1
    assert metrics["input_breakdown"]["tool_result_characters"] > 0
    assert metrics["explicit_prompt_cache"] == {
        "breakpoint_count": 1,
        "cacheable_prefix_characters": len("secret stable prefix"),
        "cacheable_prefix_approx_tokens": 5,
    }
    assert [item["name"] for item in metrics["tool_breakdown"]] == [
        "large_tool",
        "small_tool",
    ]
    assert "description" not in metrics["tool_breakdown"][0]
    assert "private description" not in str(metrics)
    assert "private user question" not in str(metrics)
    assert "private result" not in str(metrics)
    assert "secret stable prefix" not in str(metrics)
    assert "secret volatile suffix" not in str(metrics)


def test_cache_ratio_uses_only_provider_reported_tokens() -> None:
    assert cache_usage_metrics(
        {"input_tokens": 1000, "cached_input_tokens": 750}
    ) == {
        "provider_reported_cached_input_tokens": 750,
        "provider_reported_cache_ratio": 0.75,
    }
    assert cache_usage_metrics({})["provider_reported_cache_ratio"] is None


def test_largest_contributors_combines_only_names_and_sizes() -> None:
    contributors = largest_contributors(
        prompt_sections=[
            {"label": "Entities", "characters": 800},
            {"label": "User prompt", "characters": 400},
        ],
        tools=[{"name": "history", "characters": 1000}],
        input_kinds={"tool_result": {"characters": 600, "count": 1}},
        limit=3,
    )

    assert contributors == [
        {
            "category": "tool_schema",
            "name": "history",
            "characters": 1000,
            "approx_tokens": 250,
        },
        {
            "category": "system_prompt_section",
            "name": "Entities",
            "characters": 800,
            "approx_tokens": 200,
        },
        {
            "category": "provider_input",
            "name": "tool_result",
            "characters": 600,
            "approx_tokens": 150,
        },
    ]
