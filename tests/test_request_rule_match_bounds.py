"""Integration-level tests for bounded Request Rule evaluation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

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
        assert release.wait(5), "matcher was never released by the event loop"
        return None

    monkeypatch.setattr(manager, "match", blocking_match)
    monkeypatch.setattr(hass, "async_add_executor_job", asyncio.to_thread)
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
        assert not task.done(), "heartbeat must run while matching is still blocked"
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
        raise SentenceMatchLimitError(
            "Request Rule sentence matching exceeded the safe work limit"
        )

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


async def test_legacy_rules_can_be_repaired_disabled_and_backed_up() -> None:
    manager = RequestRules(
        MemoryStore(
            {"rules": [_rule(0, "(on; downstairs)"), _rule(1, "(off; upstairs)")]}
        )
    )
    await manager.async_initialize()
    await manager.async_create(_rule(2, "hello", match_type="equals"))
    await manager.async_update("rule-0", _rule(0, "on downstairs"))
    await manager.async_update("rule-1", _rule(1, "(off; upstairs)", enabled=False))
    backup = await manager.async_backup_data()
    restored = RequestRules(MemoryStore())
    await restored.async_replace_backup(backup)
    assert restored.snapshot()["rules"] == manager.snapshot()["rules"]
    assert "rule-1" in restored.snapshot()["diagnostics"]
    assert restored.match("hello").rule["id"] == "rule-2"
    assert restored.match("on downstairs").rule["id"] == "rule-0"
    with pytest.raises(ValueError, match="permutations"):
        await restored.async_update("rule-1", _rule(1, "(off; upstairs)"))


async def test_matching_uses_complete_snapshot_during_rebuild(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    manager = RequestRules(MemoryStore({"rules": [_rule(0, "turn lights on")]}))
    await manager.async_initialize()
    original_compile = module._compile_sentence_pattern
    with ThreadPoolExecutor(1) as executor:

        def compile_while_matching(phrase):
            assert (
                executor.submit(manager.match, "turn lights on").result(5).rule["id"]
                == "rule-0"
            )
            return original_compile(phrase)

        monkeypatch.setattr(module, "_compile_sentence_pattern", compile_while_matching)
        await manager.async_update("rule-0", _rule(0, "turn lights off"))
    assert manager.match("turn lights on") is None
    assert manager.match("turn lights off").rule["id"] == "rule-0"


async def test_lightweight_host_matching_is_off_loop(monkeypatch) -> None:
    from threading import get_ident

    manager = RequestRules(MemoryStore())
    event_loop_thread = get_ident()
    monkeypatch.setattr(manager, "match", lambda _text: get_ident())
    assert await manager.async_match(SimpleNamespace(), "hello") != event_loop_thread


async def test_certain_equals_winner_skips_lower_ranked_sentence_work(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    manager = RequestRules(
        MemoryStore(
            {
                "rules": [
                    _rule(0, "hello {name}"),
                    _rule(1, "hello world", match_type="equals"),
                ]
            }
        )
    )
    await manager.async_initialize()

    def unexpected(*_args):
        raise AssertionError("a lower ranked pattern cannot affect the winner")

    monkeypatch.setattr(module, "_match_compiled_sentence", unexpected)
    assert manager.match("hello world").rule["id"] == "rule-1"


async def test_higher_ranked_sentence_limit_does_not_run_broader_match(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    manager = RequestRules(
        MemoryStore(
            {
                "rules": [
                    _rule(0, "hello {name}"),
                    _rule(1, "hello", match_type="contains"),
                ]
            }
        )
    )
    await manager.async_initialize()

    def fail(*_args):
        raise SentenceMatchLimitError("safe work limit")

    monkeypatch.setattr(module, "_match_compiled_sentence", fail)
    with pytest.raises(SentenceMatchLimitError):
        manager.match("hello world")


async def test_backup_preserves_over_budget_patterns_and_enforces_new_saves(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    monkeypatch.setattr(module, "MAX_AGENT_PATTERN_STATES", 8)
    manager = RequestRules(MemoryStore())
    # Each three-word phrase compiles to six states; the second is inactive.
    await manager.async_replace_backup(
        {"rules": [_rule(0, "first long command"), _rule(1, "second long command")]}
    )
    assert set(manager.snapshot()["diagnostics"]) == {"rule-1"}
    await manager.async_create(_rule(2, "hello", match_type="equals"))
    backup = await manager.async_backup_data()
    restored = RequestRules(MemoryStore())
    await restored.async_replace_backup(backup)
    assert restored.snapshot()["rules"] == manager.snapshot()["rules"]
    with pytest.raises(ValueError, match="state limit"):
        await manager.async_create(_rule(3, "another long command"))
    # Reducing the first rule allows the preserved second rule to reactivate.
    await manager.async_update("rule-0", _rule(0, "first"))
    assert not manager.snapshot()["diagnostics"]
    assert manager.match("second long command").rule["id"] == "rule-1"


async def test_aggregate_work_budget_spans_nonmatching_patterns(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )
    from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
        MatchBudget,
        compile_sentence_pattern,
    )

    text = "do something end nope"
    single = MatchBudget()
    assert compile_sentence_pattern("do {first} end").match(text, single) is None
    manager = RequestRules(
        MemoryStore(
            {"rules": [_rule(0, "do {first} end"), _rule(1, "do {second} end")]}
        )
    )
    await manager.async_initialize()
    budget = MatchBudget(maximum=single.used + 1)
    monkeypatch.setattr(module, "MatchBudget", lambda: budget)
    with pytest.raises(SentenceMatchLimitError, match="work limit"):
        manager.match(text)
    assert budget.used == budget.maximum + 1


async def test_in_flight_match_keeps_its_original_configuration(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    manager = RequestRules(
        MemoryStore(
            {
                "rules": [
                    _rule(0, "longer unrelated command", match_type="equals"),
                    _rule(1, "hello", match_type="equals"),
                ]
            }
        )
    )
    await manager.async_initialize()
    entered, release = Event(), Event()
    original_match = module._deterministic_match

    def paused_match(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original_match(*args)

    monkeypatch.setattr(module, "_deterministic_match", paused_match)
    task = asyncio.create_task(manager.async_match(SimpleNamespace(), "hello"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await manager.async_update("rule-1", _rule(1, "goodbye", match_type="equals"))
        release.set()
        result = await task
    finally:
        release.set()
        await task
    assert result.phrase == "hello"
    assert result.rule["phrases"] == ["hello"]
    assert manager.match("hello") is None


async def test_persistence_reset_clears_matching_and_diagnostics() -> None:
    from custom_components.extended_openai_conversation_responses.persistence_hardening import (
        _reset_request_rules,
    )

    manager = RequestRules(
        MemoryStore({"rules": [_rule(0, "hello"), _rule(1, "(on; downstairs)")]})
    )
    await manager.async_initialize()
    assert manager.match("hello") is not None
    assert manager.snapshot()["diagnostics"]
    _reset_request_rules(manager)
    assert manager.match("hello") is None
    assert not manager.snapshot()["diagnostics"]
