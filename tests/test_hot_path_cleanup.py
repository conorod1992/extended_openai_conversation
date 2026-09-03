"""Regression tests for remaining conversation hot-path cleanup."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import time
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import debug, local_intents
from custom_components.extended_openai_conversation_responses.hot_path_cleanup import (
    _USAGE_PRUNE_TASK,
    install_hot_path_cleanup,
)
from custom_components.extended_openai_conversation_responses.lifecycle_optimizations import (
    _LAST_USAGE_PRUNE_DATE,
    install_lifecycle_optimizations,
)
from custom_components.extended_openai_conversation_responses import (
    lifecycle_optimizations,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    CompiledPhrase,
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
)


class DelayedStorage:
    """In-memory Store stand-in exposing Home Assistant delayed-save behavior."""

    def __init__(self) -> None:
        self.data = None
        self.immediate_saves = 0
        self.delayed: list[tuple[object, float]] = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.immediate_saves += 1
        self.data = deepcopy(data)

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.delayed.append((data_func, delay))


async def _usage_manager():
    totals = DelayedStorage()
    daily = DelayedStorage()
    details = DelayedStorage()
    manager = UsageManager(totals, daily, details, agent_subentry_id="agent")
    await manager.async_initialize()
    return manager, totals, daily, details


async def test_usage_delayed_save_builds_snapshot_only_when_store_requests_it() -> None:
    """Delayed accounting must not serialize retained history on the request path."""
    install_lifecycle_optimizations()
    install_hot_path_cleanup()
    manager, _totals, _daily, details = await _usage_manager()

    async with manager.async_run(home_assistant_conversation_id="conversation-a"):
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
        )

    assert details.immediate_saves == 0
    assert details.delayed
    data_func, _delay = details.delayed[-1]

    # If the callback had captured an eagerly-built snapshot this later in-memory
    # change would not be reflected. The delayed Store callback should instead read
    # the latest coherent manager state when persistence is actually due.
    manager.requests.clear()
    payload = data_func()
    assert payload["requests"] == []
    assert len(payload["runs"]) == 1


async def test_daily_usage_prune_is_scheduled_not_awaited_by_finalizer() -> None:
    """The first retention pass of a new UTC day should run after the user turn."""
    install_lifecycle_optimizations()
    install_hot_path_cleanup()
    manager, _totals, _daily, _details = await _usage_manager()
    old = (dt_util.utcnow() - timedelta(days=120)).isoformat()
    manager.request_retention_days = 30
    manager.requests = [
        UsageRequest(
            request_id="old-request",
            run_id="old-run",
            timestamp=old,
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        )
    ]
    setattr(manager, _LAST_USAGE_PRUNE_DATE, "1900-01-01")

    await lifecycle_optimizations._async_prune_usage_if_due(manager)

    # Scheduling itself is non-blocking, so the scan has not run in this coroutine.
    assert len(manager.requests) == 1
    task = getattr(manager, _USAGE_PRUNE_TASK)
    assert task is not None
    await task
    assert manager.requests == []


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
    install_hot_path_cleanup()
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


async def test_non_broadcast_local_intent_does_not_initialize_intercom(monkeypatch) -> None:
    """Ordinary local intents should not pay the Broadcast Store cold-load cost."""
    install_hot_path_cleanup()

    async def unexpected_intercom(_hass):
        raise AssertionError("Intercom should not be initialized")

    monkeypatch.setattr(local_intents, "async_get_intercom", unexpected_intercom)
    result = await local_intents._async_try_targeted_broadcast(
        object(), SimpleNamespace(text="turn on the kitchen light")
    )
    assert result is None


def _compiled_manager() -> RequestRules:
    manager = object.__new__(RequestRules)
    manager._wording_groups = []
    settings = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 80,
    }
    manager._compiled = [
        (
            {"match_type": "equals", "order": 0},
            settings,
            [CompiledPhrase("turn on", normalized="turn on")],
        ),
        (
            {"match_type": "contains", "order": 1},
            settings,
            [CompiledPhrase("something else", normalized="something else")],
        ),
    ]
    return manager


def test_request_rule_deterministic_match_never_runs_fuzzy_scoring(monkeypatch) -> None:
    """Existing deterministic precedence should short-circuit fuzzy work entirely."""
    install_hot_path_cleanup()
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
    install_hot_path_cleanup()
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
