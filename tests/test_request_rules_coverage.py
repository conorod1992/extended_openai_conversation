"""Focused defensive coverage for Request Rules persistence and validation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr


class MemoryStore:
    """Minimal in-memory Store stand-in for persistence-focused tests."""

    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


def local_rule(*, rule_id: str = "good-night", name: str = "Good night"):
    return {
        "id": rule_id,
        "name": name,
        "enabled": True,
        "phrases": ["good night"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": "defaults",
        "matching": dict(rr.DEFAULT_MATCHING),
        "order": 0,
    }


def routing_rule(*, rule_id: str = "route"):
    return {
        "id": rule_id,
        "name": "Route",
        "enabled": True,
        "phrases": ["quiet mode"],
        "match_type": "equals",
        "action_type": "model_routing",
        "action": {"reset": True, "scope": "request"},
        "matching_behavior": "defaults",
        "matching": dict(rr.DEFAULT_MATCHING),
        "order": 0,
    }


async def test_initialize_repairs_malformed_container_and_persists_defaults() -> None:
    store = MemoryStore(["not", "an", "object"])
    rules = rr.RequestRules(store)

    await rules.async_initialize()

    snapshot = rules.snapshot()
    assert snapshot["rules"] == []
    assert snapshot["defaults"] == rr.DEFAULT_MATCHING
    assert snapshot["wording_groups"] == list(rr.DEFAULT_WORDING_GROUPS)
    assert store.saves == 1


async def test_initialize_repairs_invalid_sections_and_duplicate_rule_ids() -> None:
    first = local_rule()
    duplicate = local_rule(name="Duplicate name")
    store = MemoryStore(
        {
            "defaults": {"fuzzy_threshold": 101},
            "wording_groups": "not-a-list",
            "rules": [first, duplicate],
        }
    )
    rules = rr.RequestRules(store)

    await rules.async_initialize()

    snapshot = rules.snapshot()
    assert snapshot["defaults"] == rr.DEFAULT_MATCHING
    assert snapshot["wording_groups"] == list(rr.DEFAULT_WORDING_GROUPS)
    assert [rule["id"] for rule in snapshot["rules"]] == ["good-night"]
    assert store.saves == 1


async def test_initialize_migrates_complete_legacy_request_route_once() -> None:
    store = MemoryStore(
        {
            "defaults": dict(rr.DEFAULT_MATCHING),
            "wording_groups": list(rr.DEFAULT_WORDING_GROUPS),
            "rules": [routing_rule()],
        }
    )
    rules = rr.RequestRules(store)

    await rules.async_initialize()

    action = rules.snapshot()["rules"][0]["action"]
    assert action["scope"] == "conversation"
    assert action["continue_to_ai"] is False
    assert action["reset"] is True
    assert store.saves == 1

    reloaded = rr.RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.snapshot()["rules"][0]["action"] == action
    assert store.saves == 1


async def test_revision_guard_rejects_bad_or_stale_writer_without_saving() -> None:
    store = MemoryStore()
    rules = rr.RequestRules(store)
    await rules.async_initialize()
    revision = rules.revision()

    with pytest.raises(ValueError, match="revision must be a string"):
        await rules.async_set_defaults(rr.DEFAULT_MATCHING, expected_revision=123)
    with pytest.raises(ValueError, match="changed in another tab"):
        await rules.async_set_defaults(rr.DEFAULT_MATCHING, expected_revision="stale")

    assert rules.revision() == revision
    assert store.saves == 0


async def test_revision_guard_accepts_current_revision_and_changes_after_save() -> None:
    store = MemoryStore()
    rules = rr.RequestRules(store)
    await rules.async_initialize()
    revision = rules.revision()

    updated = await rules.async_set_defaults(
        {**rr.DEFAULT_MATCHING, "fuzzy": True}, expected_revision=revision
    )

    assert updated["fuzzy"] is True
    assert rules.revision() != revision
    assert store.saves == 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "request_rules must be an object"),
        ({"unexpected": True}, "unknown request_rules fields"),
        ({"rules": "bad"}, "request_rules.rules must be a list"),
    ],
)
def test_backup_validation_rejects_invalid_container_shapes(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr.RequestRules.validate_backup_data(value)


def test_backup_validation_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate Request Rule id"):
        rr.RequestRules.validate_backup_data(
            {"rules": [local_rule(), local_rule(name="Duplicate")]}
        )


async def test_move_validates_direction_and_boundary_is_a_noop() -> None:
    store = MemoryStore({"rules": [local_rule()]})
    rules = rr.RequestRules(store)
    await rules.async_initialize()
    before = rules.snapshot()["rules"]
    saves = store.saves

    with pytest.raises(ValueError, match="direction must be up or down"):
        await rules.async_move("good-night", "sideways")
    result = await rules.async_move("good-night", "up")

    assert result == before[0]
    assert rules.snapshot()["rules"] == before
    assert store.saves == saves


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "matching settings must be an object"),
        ({"mystery": True}, "unknown matching settings"),
        ({"fuzzy": 1}, "fuzzy must be true or false"),
        ({"fuzzy_threshold": True}, "fuzzy_threshold must be an integer"),
        ({"fuzzy_threshold": 69}, "fuzzy_threshold must be an integer"),
    ],
)
def test_matching_settings_reject_invalid_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr.validate_matching_settings(value)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-list", "wording_groups must be a list"),
        ([{"canonical": "on"}], "needs canonical and alternatives"),
        (
            [{"canonical": "on", "alternatives": "switch on"}],
            "wording alternatives must be a list",
        ),
        (
            [{"canonical": "on", "alternatives": []}],
            "wording alternatives must contain 1 to 25 items",
        ),
        (
            [{"canonical": "!!!", "alternatives": ["switch on"]}],
            "searchable text",
        ),
    ],
)
def test_wording_group_validation_rejects_malformed_catalogs(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr.validate_wording_groups(value)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rule: rule.update(extra=True), "unknown rule fields"),
        (lambda rule: rule.update(phrases="good night"), "phrases must be a list"),
        (lambda rule: rule.update(phrases=[]), "phrases must contain"),
        (lambda rule: rule.update(match_type="regex"), "unsupported match type"),
        (lambda rule: rule.update(action_type="remote"), "unsupported action type"),
        (
            lambda rule: rule.update(matching_behavior="sometimes"),
            "matching_behavior must be defaults or custom",
        ),
        (lambda rule: rule.update(order=True), "order must be a non-negative integer"),
        (lambda rule: rule.update(enabled="yes"), "enabled must be true or false"),
    ],
)
def test_rule_validation_rejects_invalid_contract_fields(mutate, message) -> None:
    rule = local_rule()
    mutate(rule)
    with pytest.raises(ValueError, match=message):
        rr.validate_rule(rule)


def test_non_pattern_rule_rejects_captured_value_reference() -> None:
    rule = local_rule()
    rule["phrases"] = ["turn on {room}"]

    with pytest.raises(ValueError, match="require Sentence pattern matching"):
        rr.validate_rule(rule)


def test_request_only_complete_routing_rule_is_rejected() -> None:
    rule = routing_rule()
    rule["action"] = {
        "reset": True,
        "scope": "request",
        "continue_to_ai": False,
    }

    with pytest.raises(ValueError, match="Request-only routing requires Continue to AI"):
        rr.validate_rule(rule)


def test_routing_reset_discards_supplied_model_and_effort() -> None:
    rule = routing_rule()
    rule["action"] = {
        "reset": True,
        "scope": "conversation",
        "continue_to_ai": False,
        "model": "ignored-model",
        "reasoning_effort": "ignored-effort",
    }

    validated = rr.validate_rule(rule)

    assert validated["action"]["model"] is None
    assert validated["action"]["reasoning_effort"] is None
    assert validated["action"]["success_response"] == "Using the configured defaults"


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (
            {"domain": "Light", "service": "turn_on", "target": {}, "data": {}},
            "lowercase slugs",
        ),
        (
            {"domain": "light", "service": "turn_on", "target": [], "data": {}},
            "target and data must be objects",
        ),
        (
            {
                "type": "function",
                "function": "remember",
                "arguments": [],
            },
            "function arguments must be an object",
        ),
        (
            {
                "type": "function",
                "function": "remember",
                "arguments": {"bad-name": "value"},
            },
            "argument names must be simple identifiers",
        ),
        (
            {
                "type": "function",
                "function": "remember",
                "arguments": {"fact": {"source": "other", "value": "x"}},
            },
            "source must be fixed or slot",
        ),
        (
            {
                "type": "function",
                "function": "remember",
                "arguments": {
                    "fact": {"source": "slot", "slot": "fact", "extra": True}
                },
            },
            "slot arguments need only source and slot",
        ),
    ],
)
def test_local_action_migration_rejects_malformed_legacy_actions(action, message) -> None:
    rule = local_rule()
    rule["action"]["actions"] = [action]

    with pytest.raises(ValueError, match=message):
        rr.validate_rule(rule)


def test_runtime_expires_old_overrides_and_refreshes_timeout(monkeypatch) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(rr, "monotonic", lambda: clock["now"])
    runtime = rr.RequestRuleRuntime()

    runtime.set("first", {"model": "one"}, timeout_minutes=1)
    clock["now"] = 30.0
    assert runtime.get("first", timeout_minutes=2) == {"model": "one"}

    clock["now"] = 91.0
    assert runtime.get("first") == {"model": "one"}
    clock["now"] = 152.0
    assert runtime.get("first") == {}


def test_runtime_reset_request_ignores_conversation_override() -> None:
    runtime = rr.RequestRuleRuntime()
    runtime.set("session", {"model": "conversation-model"})

    effective = runtime.effective_options(
        {"model": "configured-model", "reasoning_effort": "low"},
        "session",
        {
            rr._REQUEST_RESET_SENTINEL: "1",
            "reasoning_effort": "high",
        },
    )

    assert effective == {"model": "configured-model", "reasoning_effort": "high"}


def test_runtime_reset_removes_only_requested_session() -> None:
    runtime = rr.RequestRuleRuntime()
    runtime.set("one", {"model": "first"})
    runtime.set("two", {"model": "second"})

    runtime.reset("one")

    assert runtime.get("one") == {}
    assert runtime.get("two") == {"model": "second"}
