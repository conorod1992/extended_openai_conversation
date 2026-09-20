"""Regression tests for remaining conversation hot-path cleanup."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import time
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    debug,
    local_intents,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryRecord,
)
from homeassistant.util import dt as dt_util


class BlockingStorage:
    """Store stand-in proving expiry persistence happens after the read returns."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.data = None

    async def async_load(self):
        return None

    async def async_save(self, data):
        self.started.set()
        await self.release.wait()
        self.data = deepcopy(data)


async def test_temporary_memory_expiry_save_runs_after_active_read_returns() -> None:
    """Expired facts disappear immediately while their Store write runs later."""

    store = BlockingStorage()
    manager = TemporaryMemory(store)
    now = dt_util.utcnow()
    owner_scope_id = "user:test-owner"
    manager._initialized = True
    manager._records["expired"] = TemporaryMemoryRecord(
        memory_id="expired",
        scope_id="scope",
        content="old temporary fact",
        category="general",
        source="automatic",
        expires_at=(now - timedelta(minutes=1)).isoformat(),
        created_at=(now - timedelta(hours=1)).isoformat(),
        updated_at=(now - timedelta(hours=1)).isoformat(),
        owner_scope_id=owner_scope_id,
    )

    result = await manager.async_active("scope", owner_scope_id=owner_scope_id)

    assert result == []
    assert manager.expired_pruned == 1
    assert "expired" not in manager._records
    task = manager._prune_save_task
    assert task is not None
    await asyncio.wait_for(store.started.wait(), timeout=1)
    assert not task.done()
    store.release.set()
    await task
    assert store.data == {"records": []}


class DumpCountingEvent:
    def __init__(self) -> None:
        self.calls = 0

    def model_dump(self, *, exclude_none: bool = True):
        self.calls += 1
        return {
            "type": "response.output_text.delta",
            "delta": "hello",
            "response": {
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 1,
                    "total_tokens": 4,
                }
            },
        }


def test_debug_stream_event_is_converted_once() -> None:
    """Debug instrumentation should not repeatedly model-dump one stream event."""
    event = DumpCountingEvent()
    request = debug.DebugProviderRequest(
        request_id="request",
        api_surface="responses",
        started_at=dt_util.utcnow().isoformat(),
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=time.monotonic(),
    )

    request.add_event(event)

    assert event.calls == 1
    assert request.first_text_ms is not None
    assert request.usage["total_tokens"] == 4
    assert request.response_events[0]["delta"] == "hello"


async def test_non_broadcast_local_intent_does_not_initialize_intercom(
    monkeypatch,
) -> None:
    """Ordinary local intents should not pay the Broadcast Store cold-load cost."""

    async def unexpected_intercom(_hass):
        raise AssertionError("Intercom should not be initialized")

    monkeypatch.setattr(local_intents, "async_get_intercom", unexpected_intercom)
    result = await local_intents._async_try_targeted_broadcast(
        object(), SimpleNamespace(text="turn on the kitchen light")
    )
    assert result is None


def _compiled_manager() -> RequestRules:
    manager = RequestRules(None)
    manager._wording_groups = []
    settings = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 80,
    }
    manager._rules = [
        {
            "id": str(order),
            "name": phrase,
            "enabled": True,
            "match_type": kind,
            "order": order,
            "matching_behavior": "custom",
            "matching": settings,
            "phrases": [phrase],
        }
        for order, (kind, phrase) in enumerate(
            [("equals", "turn on"), ("contains", "something else")]
        )
    ]
    manager._sort_and_compile()
    return manager


def test_request_rule_deterministic_match_never_runs_fuzzy_scoring(monkeypatch) -> None:
    """Existing deterministic precedence should short-circuit fuzzy work entirely."""
    manager = _compiled_manager()

    def unexpected_fuzzy(*_args):
        raise AssertionError("fuzzy scoring should be skipped")

    monkeypatch.setattr(request_rules, "_fuzzy_score", unexpected_fuzzy)
    match = manager.match("turn on")

    assert match is not None
    assert match.fuzzy is False
    assert match.phrase == "turn on"


def test_request_rule_fuzzy_matching_still_runs_as_fallback(monkeypatch) -> None:
    """Fuzzy behavior remains available when no deterministic candidate matches."""
    manager = _compiled_manager()
    calls = 0

    def fuzzy(_candidate: str, phrase: str, _match_type: str) -> float:
        nonlocal calls
        calls += 1
        return 95.0 if phrase == "something else" else 0.0

    monkeypatch.setattr(request_rules, "_fuzzy_score", fuzzy)
    match = manager.match("unrelated words")

    assert calls == 2
    assert match is not None
    assert match.fuzzy is True
    assert match.phrase == "something else"


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
    assert debug._event_has_text(payload) is expected


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
    assert debug._event_has_action(payload) is expected


def test_debug_usage_handles_chat_completions_names_and_invalid_values() -> None:
    assert debug._extract_usage(None) is None
    assert debug._extract_usage({"usage": "invalid"}) is None

    assert debug._extract_usage(
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
    assert debug._extract_usage(
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
