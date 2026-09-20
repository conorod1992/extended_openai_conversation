"""Tests for metadata-only payload and latency diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import payload_diagnostics
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
from custom_components.extended_openai_conversation_responses.request_diagnostics import (
    _model_requests,
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


def test_model_request_selection_ignores_embeddings() -> None:
    embedding = SimpleNamespace(api_surface="embeddings")
    responses = SimpleNamespace(api_surface="responses")
    chat = SimpleNamespace(api_surface="chat.completions")
    trace = SimpleNamespace(provider_requests=[embedding, responses, chat])

    assert _model_requests(trace) == [responses, chat]


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


def test_input_kind_classifies_provider_specific_items_and_fallbacks() -> None:
    """Provider-native items should retain useful diagnostic categories."""
    cases = [
        (None, "other"),
        ({"role": "tool"}, "tool_result"),
        ({"role": "developer"}, "developer"),
        ({"type": "function_call_output"}, "tool_result"),
        ({"type": "tool_result"}, "tool_result"),
        ({"type": "function_call"}, "function_call"),
        ({"type": "reasoning"}, "reasoning"),
        ({"type": "web_search_call"}, "web_search"),
        ({"type": "custom_provider_item"}, "custom_provider_item"),
        ({}, "other"),
    ]

    for item, expected in cases:
        assert payload_diagnostics._input_kind(item) == expected


def test_input_breakdown_aggregates_uncommon_item_kinds_and_tool_results() -> None:
    """Diagnostics should aggregate provider-native items without copying content."""
    input_value = [
        {"type": "function_call", "name": "demo", "arguments": "{}"},
        {"type": "reasoning", "summary": []},
        {"type": "web_search_call", "status": "completed"},
        {"type": "function_call_output", "output": "ok"},
        {"role": "tool", "content": "also ok"},
    ]

    result = payload_diagnostics.input_breakdown(input_value)

    assert result["items"] == 5
    assert result["by_kind"]["function_call"]["count"] == 1
    assert result["by_kind"]["reasoning"]["count"] == 1
    assert result["by_kind"]["web_search"]["count"] == 1
    assert result["by_kind"]["tool_result"]["count"] == 2
    assert result["tool_result_characters"] == result["by_kind"]["tool_result"][
        "characters"
    ]
    assert result["tool_result_approx_tokens"] == payload_diagnostics.approximate_tokens(
        result["tool_result_characters"]
    )


def test_explicit_cache_breakdown_filters_non_cache_content_and_counts_text() -> None:
    """Only explicit cache breakpoint blocks in system/developer content count."""
    input_value = [
        "not-a-message",
        {"role": "user", "content": [{"prompt_cache_breakpoint": True, "text": "x"}]},
        {"role": "system", "content": "plain text"},
        {
            "role": "system",
            "content": [
                "not-a-block",
                {"type": "input_text", "text": "uncached"},
                {"prompt_cache_breakpoint": True, "text": "cached"},
                {"prompt_cache_breakpoint": True, "text": 123},
            ],
        },
        {
            "role": "developer",
            "content": [
                {"prompt_cache_breakpoint": {}, "text": "prefix"},
                {"prompt_cache_breakpoint": False},
            ],
        },
    ]

    result = payload_diagnostics.explicit_cache_breakdown(input_value)

    assert result["breakpoint_count"] == 4
    assert result["cacheable_prefix_characters"] == len("cachedprefix")
    assert result["cacheable_prefix_approx_tokens"] == (
        payload_diagnostics.approximate_tokens(len("cachedprefix"))
    )


def test_explicit_cache_breakdown_non_list_input_has_no_breakpoints() -> None:
    assert payload_diagnostics.explicit_cache_breakdown(
        {"role": "system", "content": []}
    ) == {
        "breakpoint_count": 0,
        "cacheable_prefix_characters": 0,
        "cacheable_prefix_approx_tokens": 0,
    }


def test_tool_name_supports_direct_nested_type_and_unknown_fallbacks() -> None:
    """Tool diagnostics should recover the best metadata-only display name available."""
    cases = [
        (None, "<unknown>"),
        ({"name": "direct"}, "direct"),
        ({"name": "", "function": {"name": "nested"}}, "nested"),
        ({"function": {"name": ""}, "type": "web_search_preview"}, "web_search_preview"),
        ({"function": "not-a-mapping", "type": "custom"}, "custom"),
        ({"name": 12, "type": 7}, "7"),
        ({}, "<unknown>"),
    ]

    for tool, expected in cases:
        assert payload_diagnostics._tool_name(tool) == expected


def test_tool_breakdown_uses_name_fallbacks_and_sorts_by_schema_size() -> None:
    tools = [
        {"type": "small", "description": "x"},
        {
            "type": "function",
            "function": {"name": "nested", "description": "y" * 80},
        },
        "opaque-tool",
    ]

    result = payload_diagnostics.tool_breakdown(tools)

    assert [item["name"] for item in result] == ["nested", "small", "<unknown>"]
    assert result[0]["type"] == "function"
    assert result[-1]["type"] == "unknown"
    assert all(item["approx_tokens"] >= 0 for item in result)


def test_tool_breakdown_rejects_non_list_container() -> None:
    assert payload_diagnostics.tool_breakdown({"type": "function"}) == []


def test_provider_payload_metrics_combines_classification_cache_and_tool_fallbacks() -> None:
    """The public aggregate should expose the same focused classifications together."""
    input_value = [
        {"type": "reasoning", "summary": []},
        {
            "role": "developer",
            "content": [{"prompt_cache_breakpoint": True, "text": "cached"}],
        },
    ]
    tools = [
        {"type": "function", "function": {"name": "demo"}},
        {"type": "web_search_preview"},
    ]

    result = payload_diagnostics.provider_payload_metrics(input_value, tools)

    assert result["input_breakdown"]["by_kind"]["reasoning"]["count"] == 1
    assert result["explicit_prompt_cache"]["breakpoint_count"] == 1
    assert result["explicit_prompt_cache"]["cacheable_prefix_characters"] == len("cached")
    assert {item["name"] for item in result["tool_breakdown"]} == {
        "demo",
        "web_search_preview",
    }
