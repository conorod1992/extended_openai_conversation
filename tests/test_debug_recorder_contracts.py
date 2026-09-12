"""Contract tests for volatile request-debug recording and export."""

from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from custom_components.extended_openai_conversation_responses.debug import DebugManager


def _begin(manager: DebugManager, index: int = 0):
    return manager.begin(
        entry_id="entry-1",
        subentry_id="agent-1",
        user_input={"text": f"hello {index}"},
        incoming_conversation_id=f"conversation-{index}",
    )


def test_debug_manager_status_configure_and_clear() -> None:
    manager = DebugManager()

    assert manager.status()["enabled"] is False
    assert manager.status()["count"] == 0
    assert manager.status()["volatile"] is True

    manager.configure(enabled=True, limit=5)
    trace = _begin(manager)
    manager.finish(trace, successful=True, result={"response": "done"})

    assert manager.status()["enabled"] is True
    assert manager.status()["limit"] == 5
    assert manager.status()["count"] == 1
    assert manager.get(trace.debug_id)["result"] == {"response": "done"}
    assert manager.summaries()[0]["debug_id"] == trace.debug_id

    assert manager.clear() == 1
    assert manager.status()["count"] == 0
    assert manager.get(trace.debug_id) is None


def test_debug_manager_rejects_unknown_retention_limit() -> None:
    manager = DebugManager()

    with pytest.raises(ValueError, match="Debug run limit must be one of"):
        manager.configure(limit=7)


def test_debug_manager_retention_evicts_oldest_run() -> None:
    manager = DebugManager()
    manager.configure(enabled=True, limit=5)
    traces = []

    for index in range(6):
        trace = _begin(manager, index)
        manager.finish(trace, successful=True)
        traces.append(trace)

    summaries = manager.summaries()

    assert len(summaries) == 5
    assert manager.get(traces[0].debug_id) is None
    assert [item["debug_id"] for item in summaries] == [
        trace.debug_id for trace in reversed(traces[1:])
    ]


def test_provider_request_success_serializes_usage_and_redacts_credentials() -> None:
    manager = DebugManager()
    trace = _begin(manager)
    request = trace.start_provider_request(
        "responses",
        (),
        {
            "input": [{"role": "user", "content": "hello"}],
            "headers": {
                "Authorization": "Bearer CANARY_AUTH",
                "x-api-key": "CANARY_NESTED_KEY",
            },
        },
    )
    request.add_event(
        {
            "type": "response.completed",
            "response": {
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "total_tokens": 16,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens_details": {"reasoning_tokens": 2},
                }
            },
        }
    )
    request.finish(successful=True)
    manager.finish(trace, successful=True)

    exported = manager.get(trace.debug_id)
    assert exported is not None
    provider = exported["provider_requests"][0]

    assert provider["successful"] is True
    assert provider["usage"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "cached_input_tokens": 3,
        "reasoning_tokens": 2,
    }
    serialized = json.dumps(exported, sort_keys=True)
    assert "CANARY_AUTH" not in serialized
    assert "CANARY_NESTED_KEY" not in serialized
    assert "<redacted credential>" in serialized


def test_provider_request_error_export_redacts_credentials() -> None:
    manager = DebugManager()
    trace = _begin(manager)
    request = trace.start_provider_request(
        "responses",
        (),
        {"input": [{"role": "user", "content": "hello"}]},
    )
    error = RuntimeError(
        "provider failed at https://alice:secret-password@example.invalid/ "
        "with Authorization: Bearer super-secret-token and api_key=super-secret-key"
    )

    request.finish(successful=False, error=error)
    manager.finish(trace, successful=False, error=error)

    exported = manager.get(trace.debug_id)
    assert exported is not None
    serialized = json.dumps(exported, sort_keys=True)

    assert exported["provider_requests"][0]["error_type"] == "RuntimeError"
    assert "secret-password" not in serialized
    assert "super-secret-token" not in serialized
    assert "super-secret-key" not in serialized
    assert "[redacted]" in serialized


def test_debug_export_handles_non_json_native_values() -> None:
    manager = DebugManager()
    trace = manager.begin(
        entry_id="entry-1",
        subentry_id="agent-1",
        user_input={
            "when": datetime(2026, 9, 11, 12, 30, tzinfo=UTC),
            "binary": b"abc",
            "set": {"one", "two"},
        },
        incoming_conversation_id=None,
    )
    manager.finish(trace, successful=True, result={"values": {1, 2, 3}})

    exported = manager.get(trace.debug_id)
    assert exported is not None

    assert exported["user_input"]["when"] == "2026-09-11T12:30:00+00:00"
    assert exported["user_input"]["binary"] == "<binary payload: 3 bytes>"
    assert sorted(exported["user_input"]["set"]) == ["one", "two"]
    json.dumps(exported)
