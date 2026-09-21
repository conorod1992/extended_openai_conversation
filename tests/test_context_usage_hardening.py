from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening,
    input_footprint,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    _capture_provider_usage,
    _LOCAL_ESTIMATE_DETAIL,
    _PARTIAL_PROVIDER_USAGE_DETAIL,
    _restore_local_estimate,
    _serialized_characters,
    estimate_prepared_request,
    estimate_provider_input_tokens,
    measure_provider_input,
    usage_for_accounting,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage


def test_ascii_serialization_skips_python_character_scan(monkeypatch) -> None:
    """ASCII payloads return exact counts without calling ord per character."""
    def fail_ord(_value):
        raise AssertionError("ASCII fast path should not scan characters")

    monkeypatch.setattr(context_usage_hardening, "ord", fail_ord, raising=False)

    characters, non_ascii = _serialized_characters(
        [{"role": "user", "content": "plain ascii text"}]
    )

    assert characters > 0
    assert non_ascii == 0


def test_estimate_prepared_request_reuses_text_only_input_list(monkeypatch) -> None:
    """The normal no-attachment path does not rebuild the provider input list."""
    input_value = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
    ]
    usage = RequestUsage()
    seen = {}

    def capture(_entity, measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        seen["tools"] = tools
        seen["tool_measurement"] = tool_measurement
        return 321

    monkeypatch.setattr(input_footprint, "capture_live_footprint", capture)

    estimate_prepared_request(object(), usage, input_value, None)

    assert seen["measured"] is input_value
    assert seen["tools"] is None
    assert seen["tool_measurement"] is None
    assert usage.input_tokens == 321
    assert usage.total_tokens == 321


def test_estimate_prepared_request_materializes_non_list_iterables(monkeypatch) -> None:
    """A one-shot iterable is measured completely instead of being consumed by the scan."""
    source = (
        item
        for item in [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
        ]
    )
    usage = RequestUsage()
    seen = {}

    def capture(_entity, measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        return 111

    monkeypatch.setattr(input_footprint, "capture_live_footprint", capture)

    estimate_prepared_request(object(), usage, source, None)

    assert seen["measured"] == [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
    ]
    assert usage.input_tokens == 111


def test_estimate_prepared_request_projects_multipart_user_content(monkeypatch) -> None:
    """Attachment bytes remain excluded while text content stays exact."""
    input_value = [
        {"role": "system", "content": "System"},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Describe this"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        },
    ]
    usage = RequestUsage()
    seen = {}

    def capture(_entity, measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        return 222

    monkeypatch.setattr(input_footprint, "capture_live_footprint", capture)

    estimate_prepared_request(object(), usage, input_value, None)

    assert seen["measured"] is not input_value
    assert seen["measured"][1] == {"role": "user", "content": "Describe this"}
    assert usage.input_tokens == 222


def test_shared_provider_measurement_matches_estimator() -> None:
    input_value = [{"role": "user", "content": "Hello café"}]
    tools = [{"type": "function", "name": "lookup"}]

    input_characters, tool_characters, conservative_tokens = measure_provider_input(
        input_value,
        tools,
    )

    assert input_characters == _serialized_characters(input_value)[0]
    assert tool_characters == _serialized_characters(tools)[0]
    assert conservative_tokens == estimate_provider_input_tokens(input_value, tools)


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


def test_missing_provider_usage_keeps_local_estimate() -> None:
    usage = RequestUsage(
        input_tokens=500,
        total_tokens=500,
        details={_LOCAL_ESTIMATE_DETAIL: 500},
    )

    captured = _capture_provider_usage(usage, None)

    assert captured is False
    assert usage.input_tokens == 500
    assert usage.total_tokens == 500
    assert usage.details == {_LOCAL_ESTIMATE_DETAIL: 500}


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
    assert _PARTIAL_PROVIDER_USAGE_DETAIL not in usage.details


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


def test_output_only_provider_usage_keeps_estimate_for_context() -> None:
    usage = RequestUsage(
        input_tokens=800,
        total_tokens=800,
        details={_LOCAL_ESTIMATE_DETAIL: 800},
    )

    captured = _capture_provider_usage(
        usage,
        {"completion_tokens": 30, "total_tokens": 30},
    )

    assert captured is True
    assert usage.input_tokens == 800
    assert usage.output_tokens == 30
    assert usage.total_tokens == 30
    assert usage.details[_LOCAL_ESTIMATE_DETAIL] == 800
    assert usage.details[_PARTIAL_PROVIDER_USAGE_DETAIL] == 1

    accounted = usage_for_accounting(usage)
    assert accounted == RequestUsage(output_tokens=30, total_tokens=30)


def test_reasoning_only_provider_usage_keeps_estimate_for_context() -> None:
    usage = RequestUsage(
        input_tokens=900,
        total_tokens=900,
        details={_LOCAL_ESTIMATE_DETAIL: 900},
    )

    captured = _capture_provider_usage(
        usage,
        {"output_tokens_details": {"reasoning_tokens": 17}},
    )

    assert captured is True
    assert usage.input_tokens == 900
    assert usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert usage.reasoning_tokens == 17
    assert usage.details[_LOCAL_ESTIMATE_DETAIL] == 900
    assert usage.details[_PARTIAL_PROVIDER_USAGE_DETAIL] == 1

    accounted = usage_for_accounting(usage)
    assert accounted == RequestUsage(
        reasoning_tokens=17,
        details={"output_reasoning_tokens": 17},
    )


def test_partial_usage_can_be_reconciled_after_stock_transform_overwrite() -> None:
    usage = RequestUsage(output_tokens=25, total_tokens=25)

    captured = _capture_provider_usage(
        usage,
        {"completion_tokens": 25, "total_tokens": 25},
        local_estimate=700,
    )

    assert captured is True
    assert usage.input_tokens == 700
    assert usage.output_tokens == 25
    assert usage.total_tokens == 25
    assert usage.details[_LOCAL_ESTIMATE_DETAIL] == 700
    assert usage.details[_PARTIAL_PROVIDER_USAGE_DETAIL] == 1


def test_terminal_zero_usage_cannot_erase_local_estimate() -> None:
    usage = RequestUsage()

    # Simulate the stock terminal-event normalizer replacing the in-flight estimate
    # with an unusable all-zero provider usage object before the wrapper resumes.
    _restore_local_estimate(usage, 700)

    assert usage.input_tokens == 700
    assert usage.total_tokens == 700
    assert usage.details == {_LOCAL_ESTIMATE_DETAIL: 700}


def test_real_usage_is_never_replaced_by_local_estimate() -> None:
    usage = RequestUsage(input_tokens=123, output_tokens=10, total_tokens=133)

    _restore_local_estimate(usage, 700)

    assert usage.input_tokens == 123
    assert usage.output_tokens == 10
    assert usage.total_tokens == 133
    assert usage.details == {}


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


@pytest.mark.asyncio
async def test_chat_stream_traces_usage_attached_to_normal_choice(
    monkeypatch,
) -> None:
    usage = RequestUsage()
    traces: list[dict[str, Any]] = []
    chat_log = SimpleNamespace(
        content=[],
        async_trace=lambda payload: traces.append(payload),
    )
    chunk = SimpleNamespace(
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
        },
        choices=[SimpleNamespace()],
    )

    async def stream():
        yield chunk

    transformed = context_usage_hardening.normalized_chat_stream(chat_log, stream(), usage)
    items = [item async for item in transformed]

    assert items == [chunk]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 3
    assert usage.total_tokens == 15
    assert traces == [{"stats": {"input_tokens": 12, "output_tokens": 3}}]
