from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    RequestTokenMeasurements,
    _capture_provider_usage,
    _serialized_characters,
    estimate_prepared_request,
    estimate_provider_input_tokens,
    measure_provider_input,
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
    seen = {}

    def measure(measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        seen["tools"] = tools
        seen["tool_measurement"] = tool_measurement
        return (0, 0, 321)

    monkeypatch.setattr(context_usage_hardening, "measure_provider_input", measure)

    estimate = estimate_prepared_request(input_value, None)

    assert seen["measured"] is input_value
    assert seen["tools"] is None
    assert seen["tool_measurement"] is None
    assert estimate == 321


def test_estimate_prepared_request_materializes_non_list_iterables(monkeypatch) -> None:
    """A one-shot iterable is measured completely instead of being consumed by the scan."""
    source = (
        item
        for item in [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
        ]
    )
    seen = {}

    def measure(measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        return (0, 0, 111)

    monkeypatch.setattr(context_usage_hardening, "measure_provider_input", measure)

    estimate = estimate_prepared_request(source, None)

    assert seen["measured"] == [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
    ]
    assert estimate == 111


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
    seen = {}

    def measure(measured, tools, *, tool_measurement=None):
        seen["measured"] = measured
        return (0, 0, 222)

    monkeypatch.setattr(context_usage_hardening, "measure_provider_input", measure)

    estimate = estimate_prepared_request(input_value, None)

    assert seen["measured"] is input_value
    assert estimate == 222


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
    usage = RequestTokenMeasurements(context_estimate=500)
    original = usage.reported

    captured = _capture_provider_usage(usage, None)

    assert captured is None
    assert usage.reported is original
    assert usage.reported == RequestUsage()
    assert usage.context_tokens == 500
    assert usage.context_estimate == 500


def test_provider_usage_replaces_local_estimate() -> None:
    usage = RequestTokenMeasurements(context_estimate=999)

    captured = _capture_provider_usage(
        usage,
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
    )

    assert captured is not None
    assert usage.reported == RequestUsage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cached_input_tokens=20,
        details={"input_cached_tokens": 20},
    )
    assert usage.context_tokens == 120
    assert usage.context_estimate is None


def test_unusable_zero_provider_usage_keeps_local_estimate() -> None:
    usage = RequestTokenMeasurements(context_estimate=500)
    original = usage.reported

    captured = _capture_provider_usage(
        usage,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )

    assert captured == RequestUsage()
    assert usage.reported is original
    assert usage.reported == RequestUsage()
    assert usage.context_tokens == 500
    assert usage.context_estimate == 500


def test_output_only_provider_usage_keeps_estimate_for_context() -> None:
    usage = RequestTokenMeasurements(context_estimate=800)

    captured = _capture_provider_usage(
        usage,
        {"completion_tokens": 30, "total_tokens": 30},
    )

    assert captured is not None
    assert usage.reported == RequestUsage(output_tokens=30, total_tokens=30)
    assert usage.context_tokens == 800
    assert usage.context_estimate == 800


def test_reasoning_only_provider_usage_keeps_estimate_for_context() -> None:
    usage = RequestTokenMeasurements(context_estimate=900)

    captured = _capture_provider_usage(
        usage,
        {"output_tokens_details": {"reasoning_tokens": 17}},
    )

    assert captured is not None
    assert usage.reported == RequestUsage(
        reasoning_tokens=17,
        details={"output_reasoning_tokens": 17},
    )
    assert usage.context_tokens == 900
    assert usage.context_estimate == 900


def test_partial_usage_preserves_active_context_estimate() -> None:
    usage = RequestTokenMeasurements(context_estimate=700)

    captured = _capture_provider_usage(
        usage,
        {"completion_tokens": 25, "total_tokens": 25},
    )

    assert captured is not None
    assert usage.reported == RequestUsage(output_tokens=25, total_tokens=25)
    assert usage.context_tokens == 700
    assert usage.context_estimate == 700


def test_terminal_zero_usage_cannot_erase_local_estimate() -> None:
    usage = RequestTokenMeasurements(context_estimate=700)

    _capture_provider_usage(
        usage, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )

    assert usage.reported == RequestUsage()
    assert usage.context_tokens == 700
    assert usage.context_estimate == 700


def test_real_usage_is_never_replaced_by_local_estimate() -> None:
    reported = RequestUsage(input_tokens=123, output_tokens=10, total_tokens=133)
    usage = RequestTokenMeasurements(reported=reported)

    _capture_provider_usage(
        usage, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )

    assert usage.reported is reported
    assert usage.reported == RequestUsage(
        input_tokens=123, output_tokens=10, total_tokens=133
    )
    assert usage.context_tokens == 123
    assert usage.context_estimate is None


def test_local_estimate_is_separate_from_provider_usage() -> None:
    estimated = RequestTokenMeasurements(context_estimate=2500)
    reported = estimated.reported

    assert reported == RequestUsage()
    assert estimated.reported is reported
    assert estimated.context_tokens == 2500
    assert estimated.context_estimate == 2500
    estimated.context_estimate = 3000
    assert estimated.reported is reported
    assert reported == RequestUsage()


def test_real_provider_usage_is_preserved_for_accounting() -> None:
    reported = RequestUsage(
        input_tokens=321,
        output_tokens=45,
        total_tokens=366,
        cached_input_tokens=100,
        details={"input_cached_tokens": 100},
    )
    usage = RequestTokenMeasurements(reported=reported)

    assert usage.reported is reported
    assert usage.context_tokens == 321
    assert usage.context_estimate is None


def test_positive_provider_input_permanently_retires_context_estimate() -> None:
    usage = RequestTokenMeasurements(context_estimate=900)
    _capture_provider_usage(
        usage, {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    )
    assert usage.context_tokens == 120
    assert usage.context_estimate is None

    _capture_provider_usage(usage, {"input_tokens": 0, "output_tokens": 40})

    assert usage.reported == RequestUsage(output_tokens=40, total_tokens=40)
    assert usage.context_tokens == 40
    assert usage.context_estimate is None


def test_provider_snapshots_and_details_remain_independent() -> None:
    usage = RequestTokenMeasurements(context_estimate=900)
    other = RequestTokenMeasurements()
    initial = usage.reported
    assert initial is not other.reported
    assert initial.details is not other.reported.details
    _capture_provider_usage(
        usage,
        {
            "input_tokens": 120,
            "output_tokens": 30,
            "input_tokens_details": {"cached_tokens": 20},
        },
    )
    first = usage.reported
    _capture_provider_usage(
        usage,
        {
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 7},
        },
    )
    latest = usage.reported

    assert initial == RequestUsage()
    assert first == RequestUsage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cached_input_tokens=20,
        details={"input_cached_tokens": 20},
    )
    assert latest == RequestUsage(
        input_tokens=120,
        output_tokens=40,
        total_tokens=160,
        reasoning_tokens=7,
        details={"output_reasoning_tokens": 7},
    )
    assert initial is not first
    assert first is not latest
    assert first.details is not latest.details
    latest.details["output_reasoning_tokens"] = 8
    assert first.details == {"input_cached_tokens": 20}
    assert initial.details == other.reported.details == {}


def test_precomputed_estimate_skips_input_and_tool_measurement(monkeypatch) -> None:
    def fail_measurement(*args, **kwargs):
        raise AssertionError("A prepared estimate must not be measured again")

    class UnreadInput:
        def __iter__(self):
            raise AssertionError("A prepared estimate must not materialize input")

    monkeypatch.setattr(
        context_usage_hardening, "measure_provider_input", fail_measurement
    )
    monkeypatch.setattr(
        context_usage_hardening, "formatted_tool_measurement", fail_measurement
    )
    monkeypatch.setattr(
        context_usage_hardening, "_serialized_characters", fail_measurement
    )

    assert estimate_prepared_request(UnreadInput(), object(), input_tokens=321) == 321


@pytest.mark.asyncio
async def test_chat_stream_traces_usage_attached_to_normal_choice(
    monkeypatch,
) -> None:
    usage = RequestTokenMeasurements()
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

    transformed = context_usage_hardening.normalized_chat_stream(
        chat_log, stream(), usage
    )
    items = [item async for item in transformed]

    assert items == [chunk]
    assert usage.reported == RequestUsage(
        input_tokens=12, output_tokens=3, total_tokens=15
    )
    assert usage.context_tokens == 12
    assert traces == [{"stats": {"input_tokens": 12, "output_tokens": 3}}]
