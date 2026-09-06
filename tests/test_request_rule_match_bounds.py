"""Integration-level tests for bounded Request Rule evaluation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Event

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    SentenceMatchLimitError,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRuleStore,
    RequestRules,
    async_evaluate_rule,
)


class MemoryStore:
    """Small in-memory Store stand-in for Request Rule manager tests."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.data = deepcopy(data)
        self.saves += 1


def _rule(
    number: int,
    phrase: str,
    *,
    match_type: str = "sentence_pattern",
    enabled: bool = True,
) -> dict:
    return {
        "id": f"rule-{number}",
        "name": f"Rule {number}",
        "enabled": enabled,
        "phrases": [phrase],
        "match_type": match_type,
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.test"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": number,
    }


async def test_largest_supported_rule_set_matches_deterministically() -> None:
    """The 500-rule public limit should remain usable with sentence patterns."""
    rules = [_rule(index, f"command {index}") for index in range(500)]
    manager = RequestRules(MemoryStore({"rules": rules}))
    await manager.async_initialize()

    match = manager.match("command 499")
    assert match is not None
    assert match.rule["id"] == "rule-499"


async def test_newly_unsupported_stored_pattern_is_preserved_with_diagnostic() -> None:
    """A legacy Hassil-only pattern must stay editable instead of disappearing."""
    legacy = _rule(0, "(on; downstairs)")
    store = MemoryStore({"rules": [legacy]})
    manager = RequestRules(store)
    await manager.async_initialize()

    snapshot = manager.snapshot()
    assert [rule["id"] for rule in snapshot["rules"]] == ["rule-0"]
    assert "rule-0" in snapshot["diagnostics"]
    assert "permutations" in snapshot["diagnostics"]["rule-0"]
    assert store.data["rules"][0]["phrases"] == ["(on; downstairs)"]
    assert manager.match("on downstairs") is None


async def test_async_match_keeps_home_assistant_event_loop_responsive(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The effective live matcher seam must execute CPU work in HA's executor."""
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    started = Event()
    release = Event()

    def blocking_match(_text: str):
        started.set()
        release.wait(1)
        return None

    monkeypatch.setattr(manager, "match", blocking_match)
    task = asyncio.create_task(manager.async_match(hass, "hello"))
    try:
        for _ in range(1_000):
            if started.is_set():
                break
            await asyncio.sleep(0)
        assert started.is_set()

        heartbeat = False

        async def beat() -> None:
            nonlocal heartbeat
            heartbeat = True

        heartbeat_task = asyncio.create_task(beat())
        await asyncio.sleep(0)
        await heartbeat_task
        assert heartbeat is True
    finally:
        release.set()
        await task


async def test_match_limit_failure_runs_no_local_action(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An indeterminate bounded match must fail closed before side effects."""
    manager = RequestRules(MemoryStore({"rules": [_rule(0, "dangerous command")]}))
    await manager.async_initialize()
    action_started = False

    async def fail_match(_hass, _text: str):
        raise SentenceMatchLimitError("Request Rule sentence matching exceeded the safe work limit")

    async def unexpected_validate(*_args, **_kwargs):
        nonlocal action_started
        action_started = True
        raise AssertionError("local action validation must not start")

    monkeypatch.setattr(manager, "async_match", fail_match)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.async_validate_actions_config",
        unexpected_validate,
    )

    result = await async_evaluate_rule(
        hass,
        manager,
        RequestRuleRuntime(),
        "dangerous command",
        "conversation:test",
    )
    assert result is None
    assert action_started is False
