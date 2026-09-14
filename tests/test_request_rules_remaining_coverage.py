"""Focused coverage for remaining Request Rules behavior and defensive paths."""

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
