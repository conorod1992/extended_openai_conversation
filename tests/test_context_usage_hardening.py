from __future__ import annotations

from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    _capture_provider_usage,
    _LOCAL_ESTIMATE_DETAIL,
    estimate_provider_input_tokens,
    usage_for_accounting,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage


def test_estimate_counts_input_and_tools_conservatively() -> None:
    input_value = [
        {"role": "system", "content": "You are a home assistant."},
        {"role": "user", "content": "Which lights are on?"},
    ]
    without_tools = estimate_provider_input_tokens(input_value)
    with_tools = estimate_provider_input_tokens(
        input_value,
        [
            {
                "type": "function",
                "name": "get_state",
                "description": "Read the current state of an entity",
                "parameters": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                },
            }
        ],
    )

    assert without_tools > 0
    assert with_tools > without_tools


def test_estimate_is_more_conservative_for_non_ascii_text() -> None:
    ascii_estimate = estimate_provider_input_tokens(
        [{"role": "user", "content": "a" * 120}]
    )
    unicode_estimate = estimate_provider_input_tokens(
        [{"role": "user", "content": "界" * 120}]
    )

    assert unicode_estimate > ascii_estimate


def test_provider_usage_replaces_local_estimate() -> None:
    usage = RequestUsage(
        input_tokens=999,
        total_tokens=999,
        details={_LOCAL_ESTIMATE_DETAIL: 999},
    )

    captured = _capture_provider_usage(
        usage,
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
    )

    assert captured is True
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.total_tokens == 150
    assert usage.cached_input_tokens == 20
    assert _LOCAL_ESTIMATE_DETAIL not in usage.details


def test_unusable_zero_provider_usage_keeps_local_estimate() -> None:
    usage = RequestUsage(
        input_tokens=500,
        total_tokens=500,
        details={_LOCAL_ESTIMATE_DETAIL: 500},
    )

    captured = _capture_provider_usage(
        usage,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )

    assert captured is False
    assert usage.input_tokens == 500
    assert usage.details == {_LOCAL_ESTIMATE_DETAIL: 500}


def test_local_estimate_is_never_persisted_as_provider_usage() -> None:
    estimated = RequestUsage(
        input_tokens=2500,
        total_tokens=2500,
        details={_LOCAL_ESTIMATE_DETAIL: 2500},
    )

    accounted = usage_for_accounting(estimated)

    assert accounted == RequestUsage()
    # The in-flight object is intentionally left intact for the context-management
    # decision that runs immediately after usage accounting.
    assert estimated.input_tokens == 2500
    assert estimated.total_tokens == 2500


def test_real_provider_usage_is_preserved_for_accounting() -> None:
    reported = RequestUsage(
        input_tokens=321,
        output_tokens=45,
        total_tokens=366,
        cached_input_tokens=100,
        details={"input_cached_tokens": 100},
    )

    assert usage_for_accounting(reported) is reported
