"""Request Rules validation, pattern, migration, and defensive-boundary tests."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
    GuestModeDenied,
)
from custom_components.extended_openai_conversation_responses import request_rules as rr
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    validate_rule,
)


class MemoryStore:
    """Minimal in-memory Request Rules store with observable persistence."""

    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


def local_rule(*, name="Good night", phrases=None, match_type="equals"):
    return {
        "id": name.casefold().replace(" ", "-"),
        "name": name,
        "enabled": True,
        "phrases": phrases or ["good night"],
        "match_type": match_type,
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "action": "script.turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


async def manager(*rules):
    store = MemoryStore({"defaults": dict(DEFAULT_MATCHING), "rules": list(rules)})
    result = RequestRules(store)
    await result.async_initialize()
    return result, store


def function_rule():
    rule = local_rule(name="Call helper")
    rule["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "old_name", "arguments": {}},
        },
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "old_name", "arguments": {"value": 1}},
        },
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "other_name", "arguments": {}},
        },
    ]
    return validate_rule(rule)


async def test_function_references_and_rename_cover_exact_configured_calls() -> None:
    """References are per-rule while rename rewrites every exact call atomically."""
    rule = function_rule()
    rules, store = await manager(rule)

    assert rules.function_references("old_name") == [
        {"id": "call-helper", "name": "Call helper"}
    ]
    assert rules.function_references("missing") == []

    # A no-op rename returns before revision enforcement or persistence.
    saves_before = store.saves
    assert (
        await rules.async_rename_function_reference(
            "old_name", "old_name", expected_revision="stale-is-irrelevant"
        )
        == 0
    )
    assert store.saves == saves_before

    revision = rules.revision()
    assert (
        await rules.async_rename_function_reference(
            "old_name", "new_name", expected_revision=revision
        )
        == 2
    )
    assert store.saves == saves_before + 1
    assert rules.function_references("old_name") == []
    assert rules.function_references("new_name") == [
        {"id": "call-helper", "name": "Call helper"}
    ]

    actions = rules.snapshot()["rules"][0]["action"]["actions"]
    assert [item["data"]["function"] for item in actions] == [
        "new_name",
        "new_name",
        "other_name",
    ]

    saves_before = store.saves
    assert await rules.async_rename_function_reference("missing", "unused") == 0
    assert store.saves == saves_before


def test_sentence_variants_must_capture_identical_slots() -> None:
    rule = local_rule(
        phrases=["turn {device} on", "turn {area} on"],
        match_type="sentence_pattern",
    )
    with pytest.raises(ValueError, match="same slots"):
        validate_rule(rule)


async def test_stored_sentence_slot_mismatch_is_retained_with_diagnostic() -> None:
    """Legacy/stored rules remain repairable even when compilation rejects them."""
    rule = local_rule(
        phrases=["turn {device} on", "turn {area} on"],
        match_type="sentence_pattern",
    )
    stored_rule = validate_rule(rule, validate_sentence_pattern=False)
    rules, _store = await manager(stored_rule)

    snapshot = rules.snapshot()
    assert [item["id"] for item in snapshot["rules"]] == ["good-night"]
    assert "same slots" in snapshot["diagnostics"]["good-night"]
    assert rules.match("turn lamp on") is None


def test_slot_template_migration_recurses_through_lists_and_mappings() -> None:
    assert rr._migrate_slot_templates(
        [
            {"value_from": "slot", "slot": "room"},
            {"nested": ["brightness {level}", 7]},
        ]
    ) == [
        "{{ room }}",
        {"nested": ["brightness {{ level }}", 7]},
    ]


@pytest.mark.parametrize(
    "nested",
    ["not-a-sequence", [{"action": "light.turn_on"}, "not-a-mapping"]],
)
def test_script_action_iteration_ignores_malformed_nested_sequences(nested) -> None:
    root = {"action": "script.turn_on", "sequence": nested}
    assert list(rr._iter_script_actions([root])) == [root]


async def test_bounded_sentence_matching_failure_skips_request_rules() -> None:
    class LimitedRules:
        async def async_match(self, hass, text):
            raise rr.SentenceMatchLimitError("work budget exceeded")

    result = await async_evaluate_rule(
        SimpleNamespace(),
        LimitedRules(),
        RequestRuleRuntime(),
        "anything",
        "session",
    )
    assert result is None


async def test_guest_authorization_denial_returns_generic_unavailable(monkeypatch) -> None:
    rules, _store = await manager(validate_rule(local_rule()))

    def deny_templates(*args, **kwargs):
        raise GuestModeDenied("denied")

    monkeypatch.setattr(rr, "_resolve_guest_slot_templates", deny_templates)
    result = await async_evaluate_rule(
        SimpleNamespace(),
        rules,
        RequestRuleRuntime(),
        "good night",
        "session",
        guest_policy=GuestCapabilityPolicy(True),
    )

    assert result is not None
    assert result.consume is True
    assert result.successful is False
    assert result.response == GUEST_MODE_UNAVAILABLE


async def test_runtime_guest_denial_returns_generic_unavailable(monkeypatch) -> None:
    rules, _store = await manager(validate_rule(local_rule()))

    async def deny_validation(*args, **kwargs):
        raise GuestModeDenied("denied during runtime validation")

    monkeypatch.setattr(rr, "async_validate_actions_config", deny_validation)
    result = await async_evaluate_rule(
        SimpleNamespace(),
        rules,
        RequestRuleRuntime(),
        "good night",
        "session",
    )

    assert result is not None
    assert result.consume is True
    assert result.successful is False
    assert result.response == GUEST_MODE_UNAVAILABLE


# Defensive validation and helper boundaries consolidated from the residual layer.

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError


class ResidualMemoryStore:
    """Small persistence seam for manager tests."""

    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


def residual_local_rule(
    *,
    rule_id: str = "rule-1",
    name: str = "Rule one",
    action: dict | None = None,
    order: int = 0,
) -> dict:
    return {
        "id": rule_id,
        "name": name,
        "enabled": True,
        "phrases": ["run rule"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": action
        or {
            "actions": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen"},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(rr.DEFAULT_MATCHING),
        "order": order,
    }


def residual_function_rule(function_name: str = "weather") -> dict:
    return residual_local_rule(
        action={
            "actions": [
                {
                    "action": f"{DOMAIN}.call_function",
                    "data": {"function": function_name, "arguments": {}},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        }
    )


async def residual_initialized_manager(data=None) -> tuple[rr.RequestRules, ResidualMemoryStore]:
    store = ResidualMemoryStore(data)
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    return manager, store


async def test_store_migration_versions_and_unknown_version() -> None:
    store = object.__new__(rr.RequestRuleStore)
    old = {"rules": []}
    migrated = await store._async_migrate_func(1, 0, old)
    assert migrated["rules"] == []
    assert migrated["wording_groups"] == list(rr.DEFAULT_WORDING_GROUPS)
    assert await store._async_migrate_func(2, 0, old) is old
    assert await store._async_migrate_func(4, 0, old) is old
    with pytest.raises(NotImplementedError):
        await store._async_migrate_func(99, 0, old)


def test_compiled_and_legacy_normalization_short_circuits() -> None:
    compiled = rr.CompiledPhrase("plain", "plain")
    assert rr._match_compiled_sentence(compiled, object(), object()) is None
    assert rr._normalize_legacy_consumed_request_scope(["not", "a", "rule"]) == (
        ["not", "a", "rule"],
        False,
    )


async def test_initialize_truncates_oversized_stored_rules(monkeypatch) -> None:
    monkeypatch.setattr(rr, "MAX_RULES", 1)
    manager, store = await residual_initialized_manager(
        {
            "rules": [
                residual_local_rule(rule_id="first"),
                residual_local_rule(rule_id="second", order=1),
            ]
        }
    )
    assert [rule["id"] for rule in manager.snapshot()["rules"]] == ["first"]
    assert store.saves == 1


async def test_function_references_and_rename_cover_noop_and_save() -> None:
    manager, store = await residual_initialized_manager({"rules": [residual_function_rule()]})
    initial_saves = store.saves
    assert manager.function_references("missing") == []
    assert manager.function_references("weather") == [
        {"id": "rule-1", "name": "Rule one"}
    ]
    assert await manager.async_rename_function_reference("weather", "weather") == 0
    assert store.saves == initial_saves

    changed = await manager.async_rename_function_reference("weather", "forecast")
    assert changed == 1
    assert manager.function_references("weather") == []
    assert manager.function_references("forecast")[0]["id"] == "rule-1"
    assert store.saves == initial_saves + 1


async def test_manager_mutation_limits_and_shape_guards(monkeypatch) -> None:
    with pytest.raises(ValueError, match="Request Rule limit reached"):
        rr.RequestRules.validate_backup_data(
            {
                "rules": [
                    residual_local_rule(rule_id=str(index)) for index in range(rr.MAX_RULES + 1)
                ]
            }
        )

    manager, _store = await residual_initialized_manager({"rules": [residual_local_rule()]})
    with pytest.raises(ValueError, match="rule must be an object"):
        await manager.async_create([])
    with pytest.raises(ValueError, match="rule must be an object"):
        await manager.async_update("rule-1", [])
    with pytest.raises(ValueError, match="rule id already exists"):
        await manager.async_create(residual_local_rule())
    with pytest.raises(ValueError, match="Request Rule not found"):
        await manager.async_delete("missing")

    monkeypatch.setattr(rr, "MAX_RULES", 1)
    with pytest.raises(ValueError, match="Request Rule limit reached"):
        await manager.async_create(residual_local_rule(rule_id="new"))
    with pytest.raises(ValueError, match="Request Rule limit reached"):
        await manager.async_duplicate("rule-1")


def test_wording_rule_and_stored_slot_validation_limits() -> None:
    with pytest.raises(ValueError, match="at most 100 groups"):
        rr.validate_wording_groups(
            [
                {"canonical": f"word {index}", "alternatives": [f"term {index}"]}
                for index in range(101)
            ]
        )
    with pytest.raises(ValueError, match="rule must be an object"):
        rr.validate_rule([])

    value = {
        "slots": [
            "ignored",
            {"name": "valid"},
            {"name": "bad-name"},
            {"other": "missing"},
        ]
    }
    assert rr._stored_slot_names(value) == ["valid"]
    assert rr._stored_slot_names({"slots": "invalid"}) == []


@pytest.mark.parametrize(
    ("action_type", "value", "message"),
    [
        ("local_action", [], "action must be an object"),
        ("local_action", {"unknown": True}, "unknown local action fields"),
        ("local_action", {"actions": "bad"}, "actions must be a list"),
        ("local_action", {"actions": []}, "actions must contain"),
        ("model_routing", {"unknown": True}, "unknown model routing fields"),
        ("model_routing", {"reset": "yes"}, "reset must be true or false"),
        (
            "model_routing",
            {"model": "gpt-5.6", "continue_to_ai": "yes"},
            "continue_to_ai must be true or false",
        ),
        (
            "model_routing",
            {"model": "gpt-5.6", "scope": "forever"},
            "unsupported routing scope",
        ),
        (
            "model_routing",
            {"model": None, "reasoning_effort": None},
            "must set a model or reasoning effort",
        ),
        (
            "model_routing",
            {"reasoning_effort": "{effort}-extra"},
            "must be a single",
        ),
        (
            "model_routing",
            {"reasoning_effort": "imaginary"},
            "unsupported reasoning effort",
        ),
    ],
)
def test_action_validation_residual_edges(action_type, value, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr._validate_action(action_type, value)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "must be an object"),
        (
            {
                "domain": "light",
                "service": "turn_on",
                "target": {},
                "data": {},
                "extra": True,
            },
            "unknown Home Assistant action fields",
        ),
    ],
)
def test_ha_action_validation_residual_edges(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr._validate_ha_action(value)


@pytest.mark.parametrize(
    ("binding", "message"),
    [
        ({"source": "slot", "slot": "bad-name"}, "must name a captured value"),
        ({"source": "fixed", "value": 1, "extra": 2}, "need only source and value"),
    ],
)
def test_legacy_function_binding_residual_edges(binding, message) -> None:
    with pytest.raises(ValueError, match=message):
        rr._validate_local_action(
            {"type": "function", "function": "demo", "arguments": {"arg": binding}}
        )

    migrated = rr._validate_local_action(
        {
            "type": "function",
            "function": "demo",
            "arguments": {"plain": "{room}"},
        }
    )
    assert migrated["data"]["arguments"]["plain"] == "{{ room }}"


def test_slot_migration_resolution_and_complexity_helpers() -> None:
    assert rr._migrate_slot_templates(
        {"nested": ["hello {room}", {"value_from": "slot", "slot": "room"}]}
    ) == {"nested": ["hello {{ room }}", "{{ room }}"]}
    assert rr._legacy_action_slots([]) == set()
    assert rr._legacy_action_slots({"actions": "bad"}) == set()
    assert rr.resolve_slot_values(
        {
            "text": "hello {room}",
            "direct": {"value_from": "slot", "slot": "room"},
            "items": ["{room}", 3],
        },
        {"room": "kitchen"},
    ) == {
        "text": "hello kitchen",
        "direct": "kitchen",
        "items": ["kitchen", 3],
    }
    assert rr.resolve_function_arguments(
        {
            "captured": {"source": "slot", "slot": "room"},
            "fixed": {"source": "fixed", "value": 5},
        },
        {"room": "kitchen"},
    ) == {"captured": "kitchen", "fixed": 5}
    with pytest.raises(ValueError, match="maximum depth"):
        rr._validate_script_complexity("leaf", depth=rr.MAX_SCRIPT_DEPTH + 1)
    with pytest.raises(ValueError, match="nodes"):
        rr._validate_script_complexity(list(range(rr.MAX_SCRIPT_NODES + 1)))


def test_script_iterator_nested_and_defensive_branches(monkeypatch) -> None:
    sequence = [
        {
            "sequence": [{"action": "light.turn_on"}],
            "then": "ignored",
            "choose": [
                "ignored",
                {"sequence": [{"action": "switch.turn_on"}]},
            ],
            "repeat": {"sequence": [{"action": "cover.close_cover"}]},
        }
    ]
    actions = list(rr._iter_script_actions(sequence))
    assert [item.get("action") for item in actions] == [
        None,
        "light.turn_on",
        "switch.turn_on",
        "cover.close_cover",
    ]
    with pytest.raises(ValueError, match="maximum depth"):
        list(rr._iter_script_actions([], depth=rr.MAX_SCRIPT_DEPTH + 1))
    monkeypatch.setattr(rr, "MAX_SCRIPT_NODES", 0)
    with pytest.raises(ValueError, match="nodes"):
        list(rr._iter_script_actions([{}]))


def test_sensitive_and_guest_script_authorization_edges(monkeypatch) -> None:
    assert rr.rule_has_sensitive_actions({"action_type": "model_routing"}) is False
    assert (
        rr.rule_has_sensitive_actions(
            {
                "action_type": "local_action",
                "action": {"actions": [{"action": "cover.close_cover"}]},
            }
        )
        is True
    )

    policy = SimpleNamespace(allows_configured_tool=lambda name: name == "allowed")
    assert rr._guest_script_allowed(object(), [{"action": 4}], policy) is False
    assert (
        rr._guest_script_allowed(
            object(), [{"action": f"{DOMAIN}.call_function", "data": []}], policy
        )
        is False
    )
    assert (
        rr._guest_script_allowed(
            object(),
            [
                {
                    "action": f"{DOMAIN}.call_function",
                    "data": {"function": "denied"},
                }
            ],
            policy,
        )
        is False
    )
    assert rr._guest_script_allowed(object(), [{"event": "unsafe"}], policy) is False
    monkeypatch.setattr(
        rr, "guest_arguments_allowed_runtime", lambda *_args, **_kwargs: False
    )
    assert (
        rr._guest_script_allowed(object(), [{"action": "light.turn_on"}], policy)
        is False
    )


async def test_active_function_context_and_guest_slot_boundaries() -> None:
    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("demo", {})

    executor = AsyncMock(return_value={"ok": True})
    token = rr._ACTIVE_FUNCTION_EXECUTOR.set(executor)
    try:
        with pytest.raises(HomeAssistantError, match="must be an object"):
            await rr.async_call_active_function("demo", [])
        assert await rr.async_call_active_function("demo", {"value": 1}) == {"ok": True}
    finally:
        rr._ACTIVE_FUNCTION_EXECUTOR.reset(token)

    with pytest.raises(rr.GuestModeDenied):
        rr._resolve_guest_slot_templates("{{ missing }}", {})
    with pytest.raises(rr.GuestModeDenied):
        rr._resolve_guest_slot_templates("{{ valid }} {{ unsafe() }}", {"valid": "ok"})
    assert rr._resolve_guest_slot_templates(
        {"value": "{{ room }}", "items": ["{{ room }}", 2]}, {"room": "kitchen"}
    ) == {"value": "kitchen", "items": ["kitchen", 2]}


def test_routing_and_name_helpers_cover_remaining_guards() -> None:
    with pytest.raises(HomeAssistantError, match="Captured routing model is empty"):
        rr._resolved_routing_value("{model}", {"model": " "}, "model")
    with pytest.raises(HomeAssistantError, match="does not support reasoning"):
        rr._validate_effective_reasoning("gpt-4o", "high")
    with pytest.raises(HomeAssistantError, match="Unsupported captured reasoning"):
        rr._validate_effective_reasoning("gpt-5.6", "imaginary", captured=True)

    rules = [
        {"name": "A very long rule name copy"},
        {"name": "A very long rule name copy 2"},
    ]
    assert rr._duplicate_rule_name("A very long rule name", rules).endswith("copy 3")
    with pytest.raises(ValueError, match="too long"):
        rr._clean("x" * 6, 5, "field")


async def test_shared_manager_and_runtime_factories(monkeypatch) -> None:
    hass = SimpleNamespace(data={})
    initialize = AsyncMock()
    monkeypatch.setattr(rr, "RequestRuleStore", lambda *_args: ResidualMemoryStore(None))
    monkeypatch.setattr(rr.RequestRules, "async_initialize", initialize)
    first = await rr.async_get_request_rules(hass, "entry", "agent")
    second = await rr.async_get_request_rules(hass, "entry", "agent")
    assert first is second
    assert initialize.await_count == 2
    assert rr.get_request_rule_runtime(
        hass, "entry", "agent"
    ) is rr.get_request_rule_runtime(hass, "entry", "agent")


# Aggregate pattern-state regression consolidated from the final coverage layer.

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.extended_openai_conversation_responses import request_rules as rr


class PatternMemoryStore:
    """Minimal store for constructing a Request Rules manager."""

    async def async_load(self):
        return None

    async def async_save(self, data):
        return None


def _pattern_rule(rule_id: str, order: int, phrase: str) -> dict:
    return {
        "id": rule_id,
        "name": rule_id.replace("-", " ").title(),
        "enabled": True,
        "phrases": [phrase],
        "match_type": "sentence_pattern",
        "action_type": "local_action",
        "action": {
            "actions": [],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(rr.DEFAULT_MATCHING),
        "order": order,
    }


def test_sort_and_compile_deactivates_pattern_that_exceeds_aggregate_state_limit() -> None:
    """The per-agent state ceiling applies across enabled patterns, not just per rule."""
    manager = rr.RequestRules(PatternMemoryStore())
    first = _pattern_rule("first-pattern", 0, "first")
    overflow = _pattern_rule("overflow-pattern", 1, "overflow")
    manager._rules = [deepcopy(first), deepcopy(overflow)]

    compiled = {
        "first": rr.CompiledPhrase(
            "first",
            sentence_pattern=SimpleNamespace(
                capture_names=set(), state_count=rr.MAX_AGENT_PATTERN_STATES
            ),
        ),
        "overflow": rr.CompiledPhrase(
            "overflow",
            sentence_pattern=SimpleNamespace(capture_names=set(), state_count=1),
        ),
    }

    with patch.object(rr, "_compile_sentence_pattern", side_effect=compiled.__getitem__):
        changed = manager._sort_and_compile()

    assert changed is False
    assert manager._diagnostics == {
        "overflow-pattern": (
            "Sentence pattern is inactive: enabled sentence patterns exceed the "
            f"per-agent compiled state limit of {rr.MAX_AGENT_PATTERN_STATES}"
        )
    }
    assert [
        rule["id"] for rule, _settings, _phrase in manager._matching_snapshot.phrases
    ] == ["first-pattern"]
