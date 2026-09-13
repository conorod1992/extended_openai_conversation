"""Focused residual coverage for payload diagnostics helpers."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses import payload_diagnostics


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
