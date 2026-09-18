"""Regression tests for remaining conversation hot-path cleanup."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import time
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import (
    debug,
    local_intents,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.hot_path_cleanup import (
    install_hot_path_cleanup,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryRecord,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_performance import (
    _PRUNE_SAVE_TASK,
    install_temporary_memory_read_fast_path,
)


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
    install_temporary_memory_read_fast_path()
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
    task = getattr(manager, _PRUNE_SAVE_TASK)
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


async def test_non_broadcast_local_intent_does_not_initialize_intercom(
    monkeypatch,
) -> None:
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
