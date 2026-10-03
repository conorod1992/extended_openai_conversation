"""Generated Request Rule variants and a seeded mutable rule-set model."""

from __future__ import annotations

from copy import deepcopy
import random

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    ACTION_TYPES,
    DEFAULT_MATCHING,
    MATCH_TYPES,
    ROUTING_SCOPES,
    RequestRules,
)
from tests_stress.conftest import record

CLASSIFIED_MATCHERS = {
    "equals",
    "starts_with",
    "ends_with",
    "contains",
    "sentence_pattern",
}
CLASSIFIED_ACTIONS = {"local_action", "model_routing"}
CLASSIFIED_SCOPES = {"request", "conversation"}


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def rule(rule_id: str, phrase: str, match_type: str = "equals", **extra) -> dict:
    return {
        "id": rule_id,
        "name": rule_id,
        "phrases": [phrase],
        "match_type": match_type,
        "action_type": "local_action",
        "action": {"actions": [{"action": "script.turn_on"}]},
        "matching_behavior": "custom",
        "matching": {
            "word_forms": False,
            "wording_alternatives": False,
            "fuzzy": False,
        },
        **extra,
    }


async def manager(*rules: dict) -> RequestRules:
    result = RequestRules(MemoryStore({"rules": list(rules)}))
    await result.async_initialize()
    return result


async def test_rule_revision_detects_aba_during_suspended_writer(stress_trace) -> None:
    """The matcher may return to A while a management writer holds A's token."""
    rules = await manager(rule("initial", "turn on"))
    original = rules.revision()
    await rules.async_set_defaults({**DEFAULT_MATCHING, "fuzzy": True})
    record(stress_trace, "request_rules_to_b", revision=rules.revision())
    await rules.async_set_defaults(DEFAULT_MATCHING)
    record(stress_trace, "request_rules_to_a", revision=rules.revision())
    assert rules.revision() != original
    with pytest.raises(ValueError, match="changed in another tab"):
        await rules.async_set_defaults(
            {**DEFAULT_MATCHING, "fuzzy_threshold": 91},
            expected_revision=original,
        )
    assert rules.snapshot()["defaults"] == DEFAULT_MATCHING


def test_behavioral_inventory_is_classified() -> None:
    assert set(MATCH_TYPES) == CLASSIFIED_MATCHERS, (
        "Classify any new Request Rule matcher in enhanced matrix"
    )
    assert set(ACTION_TYPES) == CLASSIFIED_ACTIONS, (
        "Classify any new Request Rule action in enhanced matrix"
    )
    assert set(ROUTING_SCOPES) == CLASSIFIED_SCOPES, (
        "Classify any new Request Rule routing scope"
    )
    assert set(DEFAULT_MATCHING) == {
        "word_forms",
        "wording_alternatives",
        "fuzzy",
        "fuzzy_threshold",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Turn On",
            {"equals", "starts_with", "ends_with", "contains", "sentence_pattern"},
        ),
        (
            "  turn on  ",
            {"equals", "starts_with", "ends_with", "contains", "sentence_pattern"},
        ),
        ("turn on kitchen", {"starts_with", "contains"}),
        ("please turn on", {"ends_with", "contains"}),
        ("please turn on kitchen", {"contains"}),
        ("return only", set()),
        (
            "turn on!",
            {"equals", "starts_with", "ends_with", "contains", "sentence_pattern"},
        ),
        ("turn on " + "x " * 250, {"starts_with", "contains"}),
    ],
)
async def test_generated_strict_matcher_matrix(text: str, expected: set[str]) -> None:
    for match_type in MATCH_TYPES:
        rules = await manager(rule(match_type, "turn on", match_type))
        match = rules.match(text)
        assert (match is not None) == (match_type in expected), (match_type, text)
        preview = request_rule_match_preview(match)
        assert preview["matched"] == (match is not None)
        if match:
            assert preview["rule"]["id"] == match_type
            assert preview["captured_values"] == match.slots


@pytest.mark.asyncio
async def test_seeded_rule_mutation_and_persistence(
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    rng = random.Random(stress_seed ^ 0xA11CE)
    store = MemoryStore({"rules": []})
    rules = RequestRules(store)
    await rules.async_initialize()
    # Only inputs and the specified move/delete semantics update this oracle.
    # Never adopt snapshots or management return values as expected state.
    live: dict[str, dict] = {}
    for step in range(100 * stress_scale):
        operation = (
            rng.choice(("create", "create", "toggle", "delete", "move", "reload"))
            if live
            else "create"
        )
        if operation == "create" and len(live) < 30:
            identifier = f"rule-{step}"
            phrase = f"command {step} café"
            record(stress_trace, operation, id=identifier, phrase=phrase)
            expected = rule(identifier, phrase, order=len(live), enabled=True)
            expected["phrases"].append("shared command café")
            expected["action"]["success_response"] = f"Outcome {identifier}"
            await rules.async_create(
                expected,
                expected_revision=rules.revision(),
            )
            live[identifier] = deepcopy(expected)
        elif operation == "toggle":
            identifier = rng.choice(list(live))
            changed = {**live[identifier], "enabled": not live[identifier]["enabled"]}
            record(stress_trace, operation, id=identifier, enabled=changed["enabled"])
            await rules.async_update(
                identifier, changed, expected_revision=rules.revision()
            )
            live[identifier] = changed
        elif operation == "delete":
            identifier = rng.choice(list(live))
            record(stress_trace, operation, id=identifier)
            assert await rules.async_delete(
                identifier, expected_revision=rules.revision()
            )
            del live[identifier]
            for order, item in enumerate(live.values()):
                item["order"] = order
        elif operation == "move":
            identifier = rng.choice(list(live))
            direction = rng.choice(("top", "bottom", "up", "down"))
            record(stress_trace, operation, id=identifier, direction=direction)
            await rules.async_move(
                identifier, direction, expected_revision=rules.revision()
            )
            identifiers = list(live)
            index = identifiers.index(identifier)
            target = {
                "top": 0,
                "bottom": len(live) - 1,
                "up": index - 1,
                "down": index + 1,
            }[direction]
            if 0 <= target < len(live) and target != index:
                identifiers.insert(target, identifiers.pop(index))
                live = {key: live[key] for key in identifiers}
                for order, item in enumerate(live.values()):
                    item["order"] = order
        else:
            record(stress_trace, "reload")
            rules = RequestRules(store)
            await rules.async_initialize()

        snapshot = rules.snapshot()
        assert len(snapshot["rules"]) == len(live)
        assert len({item["id"] for item in snapshot["rules"]}) == len(live)
        assert [item["id"] for item in snapshot["rules"]] == list(live)
        for actual, item in zip(snapshot["rules"], live.values(), strict=True):
            for key, expected in item.items():
                if key in {"action", "matching"}:
                    assert {name: actual[key][name] for name in expected} == expected
                else:
                    assert actual[key] == expected, (step, key, actual, item)
            match = rules.match(item["phrases"][0])
            assert (match is not None) == item["enabled"]
            if match:
                assert match.rule["id"] == item["id"]
            assert request_rule_match_preview(match)["matched"] == (match is not None)
        winner = next((item for item in live.values() if item["enabled"]), None)
        shared = rules.match("shared command café")
        assert (shared is not None) == (winner is not None)
        if winner:
            assert shared.rule["id"] == winner["id"]
            assert (
                shared.rule["action"]["success_response"]
                == winner["action"]["success_response"]
            )
        backup = await rules.async_backup_data()
        cloned = RequestRules(MemoryStore({"rules": []}))
        await cloned.async_initialize()
        await cloned.async_replace_backup(backup)
        assert await cloned.async_backup_data() == backup

    record(
        stress_trace, "summary", operations=100 * stress_scale, final_rules=len(live)
    )


@pytest.mark.parametrize("broken", ["contents", "enabled", "ordering", "persistence"])
async def test_seeded_oracle_rejects_broken_mutations(monkeypatch, broken):
    """The campaign must detect faults rather than learning them as its oracle."""
    if broken == "persistence":
        original = MemoryStore.async_save

        async def lose_rules(store, data):
            await original(store, {**data, "rules": []})

        monkeypatch.setattr(MemoryStore, "async_save", lose_rules)
    elif broken == "ordering":

        async def ignore_move(*args, **kwargs):
            return {}

        monkeypatch.setattr(RequestRules, "async_move", ignore_move)
    else:
        original = (
            RequestRules.async_create
            if broken == "contents"
            else RequestRules.async_update
        )

        async def corrupt(manager, *args, **kwargs):
            value = deepcopy(args[-1])
            if broken == "contents":
                value["phrases"] = ["unrequested phrase"]
            else:
                value["enabled"] = not value["enabled"]
            return await original(manager, *args[:-1], value, **kwargs)

        monkeypatch.setattr(
            RequestRules,
            "async_create" if broken == "contents" else "async_update",
            corrupt,
        )
    with pytest.raises(AssertionError):
        await test_seeded_rule_mutation_and_persistence(42, 1, [])
