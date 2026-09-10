"""Behavioural contracts for the selective Request Rules mutation campaign.

Only storage is substituted: validation, compilation and matching are real.
These tests stop at the selected rule; no action or provider is executed.
"""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)


class MemoryStore:
    """Persist manager data without a Home Assistant storage lifecycle."""

    def __init__(self, data):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def rule(rule_id, phrase, match_type="equals", *, order=0, enabled=True, matching=None):
    return {
        "id": rule_id,
        "name": rule_id,
        "phrases": [phrase] if isinstance(phrase, str) else phrase,
        "match_type": match_type,
        "order": order,
        "enabled": enabled,
        "matching_behavior": "defaults" if matching is None else "custom",
        "matching": matching or {},
        "action_type": "local_action",
        "action": {"actions": [{"action": "script.turn_on"}]},
    }


async def manager(*rules, defaults=None):
    data = {"rules": list(rules)}
    if defaults is not None:
        data["defaults"] = defaults
    result = RequestRules(MemoryStore(data))
    await result.async_initialize()
    return result


@pytest.mark.parametrize(
    ("text", "matching_types"),
    [
        ("turn on", {"equals", "starts_with", "ends_with", "contains"}),
        ("turn on kitchen", {"starts_with", "contains"}),
        ("please turn on", {"ends_with", "contains"}),
        ("please turn on kitchen", {"contains"}),
        ("turn only", set()),
        ("return on", set()),
        ("please return only now", set()),
        ("turn", set()),
        ("", set()),
    ],
)
@pytest.mark.parametrize(
    "match_type", ["equals", "starts_with", "ends_with", "contains"]
)
async def test_text_modes_require_whole_phrase_at_the_right_position(
    text, matching_types, match_type
):
    rules = await manager(rule("command", "turn on", match_type))
    assert (rules.match(text) is not None) == (match_type in matching_types)


@pytest.mark.parametrize(
    ("higher_type", "higher_phrase", "lower_type", "lower_phrase"),
    [
        ("equals", "turn on kitchen", "sentence_pattern", "turn on {room}"),
        ("sentence_pattern", "turn on {room}", "starts_with", "turn on kitchen"),
        ("starts_with", "turn", "ends_with", "on kitchen"),
        ("ends_with", "kitchen", "contains", "turn on kitchen"),
    ],
)
async def test_strict_type_rank_beats_length_and_earlier_order(
    higher_type, higher_phrase, lower_type, lower_phrase
):
    rules = await manager(
        rule("earlier", lower_phrase, lower_type),
        rule("winner", higher_phrase, higher_type, order=1),
    )
    match = rules.match("turn on kitchen")
    assert match is not None
    assert match.rule["id"] == "winner"
    assert match.fuzzy is False


async def test_longer_strict_phrase_beats_order_then_order_breaks_equal_ties():
    rules = await manager(
        rule("short", "turn", "contains"),
        rule("first-long", "turn on", "contains", order=1),
        rule("second-long", "turn on", "contains", order=2),
    )
    assert rules.match("please turn on kitchen").rule["id"] == "first-long"
    await rules.async_move("second-long", "up")
    assert rules.match("please turn on kitchen").rule["id"] == "second-long"


async def test_nonmatching_higher_ranked_rules_fall_through_to_later_variant():
    rules = await manager(
        rule("exact-miss", "turn off kitchen"),
        rule("pattern-miss", "turn off {room}", "sentence_pattern", order=1),
        rule("winner", ["turn off", "turn on"], "starts_with", order=2),
    )
    match = rules.match("turn on kitchen")
    assert match is not None
    assert match.rule["id"] == "winner"
    assert match.phrase == "turn on"


@pytest.mark.parametrize("match_type", ["equals", "sentence_pattern"])
async def test_disabled_rule_is_skipped_and_enabling_restores_precedence(match_type):
    disabled = rule("specific", "turn on kitchen", match_type, enabled=False)
    rules = await manager(disabled, rule("fallback", "turn on", "contains", order=1))
    assert rules.match("turn on kitchen").rule["id"] == "fallback"
    await rules.async_update("specific", {**disabled, "enabled": True})
    assert rules.match("turn on kitchen").rule["id"] == "specific"


@pytest.mark.parametrize(
    "rules", [[], [rule("disabled", "hello", enabled=False)], [rule("miss", "goodbye")]]
)
async def test_no_eligible_rule_returns_no_match(rules):
    assert (await manager(*rules)).match("hello") is None


@pytest.mark.parametrize(
    "setting,phrase,text",
    [
        ("word_forms", "lights", "light"),
        ("wording_alternatives", "turn on tv", "switch on television"),
    ],
)
@pytest.mark.parametrize("default_enabled", [False, True])
async def test_custom_matching_overrides_defaults_in_both_directions(
    setting, phrase, text, default_enabled
):
    rules = await manager(
        rule("custom", phrase, matching={setting: not default_enabled}),
        rule("inherited", phrase, order=1),
        defaults={setting: default_enabled},
    )
    match = rules.match(text)
    assert match is not None
    assert match.rule["id"] == ("inherited" if default_enabled else "custom")


async def test_sentence_match_can_have_no_captures_or_return_captured_values():
    rules = await manager(
        rule("literal", "good night", "sentence_pattern"),
        rule("capture", "remember {fact}", "sentence_pattern", order=1),
    )
    literal = rules.match("good night")
    assert literal is not None and literal.slots == {}
    captured = rules.match("remember buy oat milk")
    assert captured is not None
    assert captured.rule["id"] == "capture"
    assert captured.slots == {"fact": "buy oat milk"}
    assert captured.fuzzy is False
    assert rules.match("recall buy oat milk") is None


@pytest.mark.parametrize("text", ["switch on light", "turn on lights", "turn on ligt"])
async def test_sentence_patterns_ignore_tolerant_text_settings(text):
    rules = await manager(
        rule("pattern", "turn on light", "sentence_pattern"),
        defaults={"fuzzy": True, "fuzzy_threshold": 70},
    )
    assert rules.match(text) is None


async def test_strict_broad_match_beats_fuzzy_equals():
    rules = await manager(
        rule("fuzzy", "good knight", matching={"fuzzy": True, "fuzzy_threshold": 70}),
        rule("strict", "night", "contains", order=1),
    )
    match = rules.match("good night")
    assert match is not None
    assert match.rule["id"] == "strict"
    assert match.fuzzy is False


@pytest.mark.parametrize("threshold,expected", [(79, True), (80, True), (81, False)])
async def test_fuzzy_threshold_is_inclusive(threshold, expected):
    # One substituted letter in five gives 80%, exactly representable; no mock
    # score or approximate threshold hides the inclusive policy boundary.
    rules = await manager(
        rule("fuzzy", "light", matching={"fuzzy": True, "fuzzy_threshold": threshold})
    )
    match = rules.match("liagt")
    assert (match is not None) is expected
    if expected:
        assert match.fuzzy is True


async def test_fuzzy_is_opt_in():
    rules = await manager(rule("default", "light"))
    assert rules.match("liagt") is None


async def test_fuzzy_higher_score_beats_type_rank_and_order():
    rules = await manager(
        rule("lower", "light axc", matching={"fuzzy": True, "fuzzy_threshold": 70}),
        rule(
            "higher",
            "light",
            "starts_with",
            order=1,
            matching={"fuzzy": True, "fuzzy_threshold": 70},
        ),
    )
    assert rules.match("liagt abcd").rule["id"] == "higher"


async def test_fuzzy_equal_score_uses_type_then_rule_order():
    rules = await manager(
        rule(
            "broad",
            "light",
            "contains",
            matching={"fuzzy": True, "fuzzy_threshold": 70},
        ),
        rule(
            "first", "light", order=1, matching={"fuzzy": True, "fuzzy_threshold": 70}
        ),
        rule(
            "second", "light", order=2, matching={"fuzzy": True, "fuzzy_threshold": 70}
        ),
    )
    assert rules.match("liagt").rule["id"] == "first"
    await rules.async_move("second", "up")
    assert rules.match("liagt").rule["id"] == "second"
