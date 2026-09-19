"""Residual coverage for hot-path cleanup fallback behavior."""

from __future__ import annotations

import time

import pytest

from custom_components.extended_openai_conversation_responses import debug
from homeassistant.util import dt as dt_util


def test_debug_event_records_first_action_latency_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action latency is captured on the first tool/search event and remains stable."""
    monkeypatch.setattr(
        debug.DebugProviderRequest, "add_event", debug.DebugProviderRequest.add_event
    )
    request = debug.DebugProviderRequest(
        request_id="request",
        api_surface="responses",
        started_at=dt_util.utcnow().isoformat(),
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=time.monotonic() - 0.05,
    )

    request.add_event({"type": "response.function_call.arguments.delta", "delta": "{}"})
    first_action_ms = request.first_action_ms
    request.add_event({"type": "response.web_search_call.completed"})

    assert first_action_ms is not None
    assert request.first_action_ms == first_action_ms
    assert len(request.response_events) == 2
    assert (
        request.response_events[0]["type"] == "response.function_call.arguments.delta"
    )
