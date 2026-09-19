"""Focused residual coverage for conversation hot-path cleanup."""

from __future__ import annotations

import time

import pytest

from custom_components.extended_openai_conversation_responses import (
    debug,
    hot_path_cleanup,
)
from homeassistant.util import dt as dt_util


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        ({"type": "response.output_text.delta", "delta": "hello"}, True),
        ({"type": "response.output_text.delta", "delta": ""}, False),
        ({"choices": [{"delta": {"content": "hello"}}]}, True),
        ({"choices": [None, {"delta": {"content": ""}}]}, False),
        ({"choices": "invalid"}, False),
    ],
)
def test_debug_event_has_text_variants(payload: object, expected: bool) -> None:
    assert hot_path_cleanup._debug_event_has_text(payload) is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        ({"type": "response.function_call_arguments.delta"}, True),
        ({"type": "response.web_search_call.completed"}, True),
        ({"item": {"type": "function_call"}}, True),
        ({"item": {"type": "web_search_call"}}, True),
        ({"item": {"type": "message"}}, False),
        ({"item": "invalid"}, False),
    ],
)
def test_debug_event_has_action_variants(payload: object, expected: bool) -> None:
    assert hot_path_cleanup._debug_event_has_action(payload) is expected


def test_debug_usage_handles_chat_completions_names_and_invalid_values() -> None:
    assert hot_path_cleanup._debug_usage(None) is None
    assert hot_path_cleanup._debug_usage({"usage": "invalid"}) is None

    assert hot_path_cleanup._debug_usage(
        {
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": -1,
                "prompt_tokens_details": {"cached_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
            }
        }
    ) == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "cached_input_tokens": 2,
        "reasoning_tokens": 1,
    }


def test_debug_usage_reads_nested_response_and_rejects_non_integer_counts() -> None:
    assert hot_path_cleanup._debug_usage(
        {
            "response": {
                "usage": {
                    "input_tokens": "5",
                    "output_tokens": None,
                    "total_tokens": 9,
                    "input_tokens_details": [],
                    "output_tokens_details": "invalid",
                }
            }
        }
    ) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 9,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }


def _debug_request() -> debug.DebugProviderRequest:
    return debug.DebugProviderRequest(
        request_id="request",
        api_surface="responses",
        started_at=dt_util.utcnow().isoformat(),
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=time.monotonic(),
    )


def test_debug_add_event_stops_after_already_truncated() -> None:
    request = _debug_request()
    request.response_events_truncated = True
    request.response_events = [{"existing": True}]

    request.add_event({"type": "response.output_text.delta", "delta": "hello"})

    assert request.first_event_ms is not None
    assert request.first_text_ms is not None
    assert request.response_events == [{"existing": True}]


def test_debug_add_event_marks_size_overflow_without_retaining_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _debug_request()
    monkeypatch.setattr(debug, "DEBUG_MAX_EVENT_BYTES", 1)

    request.add_event({"type": "message", "content": "too large"})

    assert request.response_events_truncated is True
    assert request.response_events == []
    assert request._event_bytes == 0
