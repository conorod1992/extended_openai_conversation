"""Tests for bounded management projection of volatile request debug captures."""

from __future__ import annotations

import json

from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.debug_management_projection import (
    debug_run_summaries,
    debug_trace_page,
)
from custom_components.extended_openai_conversation_responses.management_result_limits import (
    MANAGEMENT_DEBUG_PAGE_CHARACTERS,
    MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS,
)


def _captured_manager() -> tuple[DebugManager, str]:
    manager = DebugManager()
    manager.configure(enabled=True, limit=10)
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={"text": "hello"},
        incoming_conversation_id="conversation",
    )
    trace.system_prompt = "system " + ("s" * 100_000)
    trace.memory = {"retrieved": "m" * 100_000}
    trace.result = {"speech": "r" * 100_000}
    trace.error = {"message": "e" * 10_000}
    trace.continuity = {
        "resolved_conversation_id": "resolved",
        "resumed": True,
        "mode": "device",
    }
    for index in range(7):
        request = trace.start_provider_request(
            "responses",
            (),
            {
                "model": "gpt-test",
                "input": "i" * 100_000,
                "tools": [{"name": f"tool-{index}", "description": "t" * 5_000}],
            },
        )
        request.response_events = [{"type": "delta", "delta": "o" * 100_000}]
        request._event_bytes = 100_000
        request.successful = True
    manager.finish(trace, successful=True)
    return manager, trace.debug_id


def test_debug_trace_provider_requests_are_paged_and_aggregate_bounded() -> None:
    manager, debug_id = _captured_manager()

    first = debug_trace_page(manager, debug_id, provider_offset=0, provider_limit=2)
    assert first is not None
    provider_meta = first["management_projection"]["provider_requests"]
    assert len(first["provider_requests"]) == 2
    assert provider_meta == {
        "offset": 0,
        "limit": 2,
        "returned": 2,
        "has_more": True,
        "next_offset": 2,
        "total": 7,
    }
    assert first["management_projection"]["truncated"] is True
    assert first["management_projection"]["page"]["limit_characters"] == (
        MANAGEMENT_DEBUG_PAGE_CHARACTERS
    )
    assert first["management_projection"]["page"]["remaining_characters"] >= 0
    assert first["management_projection"]["system_prompt"]["truncated"] is True
    # The hard content budget deliberately leaves only modest JSON-structure overhead.
    assert len(json.dumps(first)) < MANAGEMENT_DEBUG_PAGE_CHARACTERS + 100_000

    second = debug_trace_page(manager, debug_id, provider_offset=2, provider_limit=2)
    assert second is not None
    second_meta = second["management_projection"]["provider_requests"]
    assert second_meta["offset"] == 2
    assert second_meta["next_offset"] == 4
    assert second_meta["total"] == 7
    assert [item["request_id"] for item in first["provider_requests"]] != [
        item["request_id"] for item in second["provider_requests"]
    ]


def test_debug_run_summary_cannot_return_an_unbounded_error_object() -> None:
    manager, _debug_id = _captured_manager()

    summary = debug_run_summaries(manager)[0]

    assert summary["error_meta"]["limit_characters"] == (
        MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS
    )
    assert summary["error_meta"]["truncated"] is True
    assert len(json.dumps(summary["error"])) < MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS + 500
    assert summary["provider_request_count"] == 7
    assert summary["continuity_mode"] == "device"
