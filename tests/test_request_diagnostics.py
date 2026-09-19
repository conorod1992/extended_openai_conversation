"""Tests for request payload/latency diagnostics safety contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
import json

from custom_components.extended_openai_conversation_responses import (
    debug,
    request_diagnostics,
)


class _Marker(Enum):
    VALUE = "value"


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-1",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-11T12:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )


def test_trace_as_dict_with_diagnostics_empty_trace() -> None:
    trace = _trace()

    result = trace.as_dict()

    assert result["debug_id"] == "debug-1"
    assert result["payload_latency_diagnostics"]["model_request_count"] == 0
    assert result["payload_latency_diagnostics"]["embedding_request_count"] == 0
    assert result["payload_latency_diagnostics"]["preparation"] == {}
    assert result["payload_latency_diagnostics"]["function_tool_calls"] == []
    assert (
        result["payload_latency_diagnostics"][
            "largest_first_model_request_contributors"
        ]
        == []
    )


def test_trace_as_dict_with_diagnostics_populated_payloads() -> None:
    trace = _trace()
    trace.phases_ms = {"provider": 25, "prompt": 5}
    trace.memory = {
        "visible": {"count": 1},
        request_diagnostics._INTERNAL_PREPARATION: {
            "prompt_render_core": {
                "calls": 1,
                "total_ms": 4,
                "max_ms": 4,
                "last_count": None,
            }
        },
        request_diagnostics._INTERNAL_TOOL_CALLS: [
            {
                "name": "demo",
                "implementation_type": "native",
                "duration_ms": 3,
                "successful": True,
                "result_characters": 2,
            }
        ],
    }
    original_memory = deepcopy(trace.memory)

    request = trace.start_provider_request(
        "responses",
        (),
        {
            "input": [{"role": "user", "content": "hello world"}],
            "tools": [
                {
                    "type": "function",
                    "name": "demo",
                    "parameters": {"type": "object"},
                }
            ],
        },
    )

    result = trace.as_dict()

    assert trace.memory == original_memory
    assert result["memory"] == {"visible": {"count": 1}}
    assert request.metrics["approx_input_tokens"] > 0
    assert request.metrics["tool_breakdown"][0]["name"] == "demo"
    diagnostics = result["payload_latency_diagnostics"]
    assert diagnostics["model_request_count"] == 1
    assert (
        diagnostics["preparation"]
        == original_memory[request_diagnostics._INTERNAL_PREPARATION]
    )
    assert (
        diagnostics["function_tool_calls"]
        == original_memory[request_diagnostics._INTERNAL_TOOL_CALLS]
    )
    assert diagnostics["slowest_phases"][0] == {
        "name": "provider",
        "duration_ms": 25,
    }


def test_trace_as_dict_falls_back_when_trace_serialization_raises(monkeypatch) -> None:
    def broken_as_dict(_value, **_kwargs) -> dict[str, object]:
        raise RuntimeError("serializer failed with SECRET_RAW_PAYLOAD")

    monkeypatch.setattr(debug, "_jsonable", broken_as_dict)

    result = _trace().as_dict()

    assert result == {
        "debug_id": "debug-1",
        "diagnostics_error": "trace_serialization_failed",
    }
    assert "SECRET_RAW_PAYLOAD" not in json.dumps(result)


def test_trace_as_dict_diagnostics_failure_uses_safe_serialized_base(
    monkeypatch,
) -> None:
    trace = _trace()
    trace.memory = {"api_key": "CANARY_SECRET", "ordinary": "safe"}

    def broken_largest_contributors(**_kwargs):
        raise RuntimeError("diagnostics failed")

    monkeypatch.setattr(
        request_diagnostics, "largest_contributors", broken_largest_contributors
    )

    result = trace.as_dict()
    serialized = json.dumps(result, sort_keys=True)

    assert result["payload_latency_diagnostics"] == {
        "diagnostics_error": "payload_diagnostics_failed"
    }
    assert result["memory"]["ordinary"] == "safe"
    assert "CANARY_SECRET" not in serialized


def test_trace_as_dict_never_leaks_nested_secrets() -> None:
    trace = _trace()
    secrets = {
        "authorization": "CANARY_AUTHORIZATION",
        "api_key": "CANARY_API_KEY",
        "x-api-key": "CANARY_NESTED_KEY",
        "proxy_authorization": "CANARY_PROXY_AUTH",
    }

    trace.start_provider_request(
        "responses",
        (),
        {
            "input": [
                {
                    "role": "user",
                    "content": "hello",
                    "metadata": {
                        "credentials": {
                            "api_key": secrets["api_key"],
                            "nested": [
                                {"proxy_authorization": secrets["proxy_authorization"]}
                            ],
                        }
                    },
                }
            ],
            "headers": {"Authorization": secrets["authorization"]},
            "tools": [
                {
                    "type": "function",
                    "name": "demo",
                    "metadata": {"x-api-key": secrets["x-api-key"]},
                }
            ],
        },
    )

    serialized = json.dumps(trace.as_dict(), sort_keys=True)

    for secret in secrets.values():
        assert secret not in serialized


def test_trace_as_dict_handles_non_json_native_values() -> None:
    trace = _trace()
    value = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
    trace.memory = {"when": value, "marker": _Marker.VALUE}

    result = trace.as_dict()

    assert result["memory"]["when"] == value.isoformat()
    assert isinstance(result["memory"]["marker"], dict)
    json.dumps(result)


def test_request_diagnostics_metric_failure_does_not_break_recording(
    monkeypatch,
) -> None:
    trace = _trace()

    def broken_metrics(_input, _tools):
        raise RuntimeError("metric helper failed")

    monkeypatch.setattr(request_diagnostics, "provider_payload_metrics", broken_metrics)

    request = trace.start_provider_request(
        "responses", (), {"input": [{"role": "user", "content": "hello"}]}
    )

    assert trace.provider_requests == [request]
    assert request.api_surface == "responses"
    assert request.metrics["request_characters"] > 0
    assert "approx_input_tokens" not in request.metrics
