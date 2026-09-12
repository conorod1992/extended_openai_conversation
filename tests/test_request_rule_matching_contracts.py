"""Behavioural contracts for the selective Request Rules mutation campaign.

Only storage is substituted: validation, compilation and matching are real.
These tests stop at the selected rule; no action or provider is executed.
"""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
)
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
        ("word_forms", "light", "lights"),
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
    assert literal.phrase == "good night"
    assert literal.score == 100
    captured = rules.match("remember buy oat milk")
    assert captured is not None
    assert captured.rule["id"] == "capture"
    assert captured.slots == {"fact": "buy oat milk"}
    assert captured.fuzzy is False
    assert captured.phrase == "remember {fact}"
    assert captured.score == 100
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
    assert match.score == 100


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
        assert match.phrase == "light"
        assert threshold <= match.score <= 100


@pytest.mark.parametrize("enabled", [False, True])
async def test_inherited_settings_ignore_saved_custom_values(enabled):
    inherited = rule("inherited", "light", matching={"word_forms": not enabled})
    inherited["matching_behavior"] = "defaults"
    rules = await manager(inherited, defaults={"word_forms": enabled})
    assert (rules.match("lights") is not None) is enabled


async def test_custom_wording_groups_apply_to_phrases_and_requests():
    rules = await manager(rule("command", "activate kitchen"))
    await rules.async_set_wording_groups(
        [{"canonical": "activate", "alternatives": ["power up"]}]
    )
    assert rules.match("power up kitchen").rule["id"] == "command"
    await rules.async_update("command", rule("command", "power up kitchen"))
    assert rules.match("activate kitchen").rule["id"] == "command"


@pytest.mark.parametrize("earlier_type", ["equals", "sentence_pattern"])
async def test_fuzzy_fallback_skips_ineligible_earlier_candidates(earlier_type):
    rules = await manager(
        rule("strict-only", "good night", earlier_type),
        rule(
            "fuzzy", "light", order=1, matching={"fuzzy": True, "fuzzy_threshold": 70}
        ),
    )
    assert rules.match("liagt").rule["id"] == "fuzzy"


@pytest.mark.parametrize(
    "match_type,text",
    [
        ("equals", "please liagt"),
        ("starts_with", "please liagt"),
        ("ends_with", "liagt please"),
    ],
)
async def test_fuzzy_modes_do_not_match_the_wrong_position(match_type, text):
    rules = await manager(
        rule(
            "fuzzy",
            "light",
            match_type,
            matching={"fuzzy": True, "fuzzy_threshold": 70},
        )
    )
    assert rules.match(text) is None


async def test_equal_fuzzy_phrase_variants_keep_the_first_variant():
    rules = await manager(
        rule(
            "fuzzy", ["light", "liant"], matching={"fuzzy": True, "fuzzy_threshold": 70}
        )
    )
    match = rules.match("liagt")
    assert match is not None
    assert match.phrase == "light"


async def test_sparse_saved_order_preserves_the_winner():
    rules = await manager(
        rule("later", "hello", order=20), rule("first", "hello", order=10)
    )
    assert rules.match("hello").rule["id"] == "first"


async def test_inactive_legacy_pattern_does_not_hide_later_valid_rule():
    rules = await manager(
        rule("legacy", "(on; downstairs)", "sentence_pattern"),
        rule("valid", "hello", order=1),
    )
    assert rules.match("hello").rule["id"] == "valid"
    assert rules.match("on downstairs") is None


async def test_sentence_variants_can_match_a_later_phrase_with_the_same_slots():
    rules = await manager(
        rule("capture", ["remember {fact}", "save {fact}"], "sentence_pattern")
    )
    match = rules.match("save buy oat milk")
    assert match is not None
    assert match.phrase == "save {fact}"
    assert match.slots == {"fact": "buy oat milk"}


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


@pytest.mark.parametrize(
    "phrase,text,expected",
    [
        pytest.param("party", "parties", True, id="ies-to-y"),
        pytest.param("box", "boxes", True, id="es-ending"),
        pytest.param("glass", "glasses", True, id="preserve-double-s"),
        pytest.param("statu", "status", False, id="preserve-us"),
        pytest.param("analysi", "analysis", False, id="preserve-is"),
        pytest.param("ga", "gas", False, id="short-word-not-singularized"),
        pytest.param("part", "parties", False, id="not-just-strip-ies"),
        pytest.param("café", "  CAFE\u0301!  ", True, id="unicode-case-spacing"),
    ],
)
async def test_rule_condition_truth_table(phrase, text, expected):
    rules = await manager(rule("command", phrase))
    before = deepcopy(rules.snapshot())
    match = rules.match(text)
    assert (match is not None) is expected
    if expected:
        assert match.phrase == phrase
        assert match.fuzzy is False
        assert match.score == 100
    assert rules.snapshot() == before
    assert rules.match(text) == match


@pytest.mark.parametrize(
    "pattern,text,slots",
    [
        pytest.param("set {level=10..20}", "set ten", None, id="non-numeric"),
        pytest.param("set {level=10..20}", "set 9", None, id="below-minimum"),
        pytest.param("set {level=10..20}", "set 10", {"level": "10"}, id="minimum"),
        pytest.param("set {level=10..20}", "set 20", {"level": "20"}, id="maximum"),
        pytest.param("set {level=10..20}", "set 21", None, id="above-maximum"),
        pytest.param(
            "set {level=-20..-10}", "set -9", None, id="negative-above-maximum"
        ),
        pytest.param(
            "set {level=-20..-10}", "set -10", {"level": "-10"}, id="negative-maximum"
        ),
        pytest.param(
            "set {level=-20..-10}", "set -20", {"level": "-20"}, id="negative-minimum"
        ),
        pytest.param(
            "set {level=-20..-10}", "set -21", None, id="negative-below-minimum"
        ),
        pytest.param(
            "remember [for {person}]",
            "remember",
            {"person": ""},
            id="omitted-optional-capture",
        ),
        pytest.param(
            "remember [for {person}]",
            "remember for Alex",
            {"person": "Alex"},
            id="present-optional-capture",
        ),
        pytest.param(
            "remember [for {person}]",
            "remember for",
            None,
            id="present-capture-requires-text",
        ),
        pytest.param(
            "say {choice=ß|x}", "say SS", {"choice": "SS"}, id="enum-casefold-display"
        ),
        pytest.param(
            "say {choice=s|x}{tail}",
            "say ß",
            None,
            id="enum-cannot-split-display-character",
        ),
    ],
)
async def test_sentence_condition_truth_table(pattern, text, slots):
    rules = await manager(rule("command", pattern, "sentence_pattern"))
    before = deepcopy(rules.snapshot())
    match = rules.match(text)
    if slots is None:
        assert match is None
    else:
        assert match is not None
        assert match.slots == slots
        assert match.phrase == pattern
        assert match.score == 100
        assert match.fuzzy is False
    assert rules.snapshot() == before
    assert rules.match(text) == match


@pytest.mark.parametrize(
    "pattern,error",
    [
        pytest.param("say \\", "ends with an escape", id="unfinished-escape"),
        pytest.param("say ]", "unexpected", id="unmatched-closing-delimiter"),
        pytest.param("say [hello", "missing closing", id="unclosed-optional"),
        pytest.param("say (hello|)", "choices cannot be empty", id="empty-alternative"),
        pytest.param("say {value", "missing closing", id="unclosed-capture"),
        pytest.param("say {}", "name is required", id="unnamed-capture"),
        pytest.param("say {1value}", "names must start", id="invalid-capture-name"),
        pytest.param(
            "say {value} to {value}", "used more than once", id="duplicate-capture"
        ),
        pytest.param("say {value=}", "needs a constraint", id="empty-constraint"),
        pytest.param("say {value=20..10}", "minimum greater", id="inverted-range"),
        pytest.param("say {value=a|}", "empty choice", id="empty-enum-choice"),
    ],
)
async def test_invalid_pattern_uses_current_failure_contract(pattern, error):
    rules = await manager(rule("existing", "hello"))
    before = deepcopy(rules.snapshot())
    with pytest.raises(ValueError, match=error):
        await rules.async_create(rule("invalid", pattern, "sentence_pattern"))
    assert rules.snapshot() == before
    assert rules.match("hello").rule["id"] == "existing"
    assert rules.match("say anything") is None


@pytest.mark.parametrize(
    "values,error",
    [
        pytest.param({}, "phrases must be a list", id="omitted"),
        pytest.param({"phrases": None}, "phrases must be a list", id="null"),
        pytest.param({"phrases": "hello"}, "phrases must be a list", id="scalar"),
        pytest.param({"phrases": ""}, "phrases must be a list", id="empty-scalar"),
        pytest.param({"phrases": []}, "phrases must contain", id="empty-list"),
        pytest.param({"phrases": [""]}, "phrase is required", id="empty-item"),
        pytest.param({"phrases": ["  "]}, "phrase is required", id="whitespace-item"),
    ],
)
async def test_empty_vs_omitted_condition_values(values, error):
    # The rule language requires a nonempty list, not a scalar condition. These
    # errors belong to integration validation before storage or matching changes.
    rules = await manager(rule("existing", "hello"))
    definition = rule("invalid", "unused")
    del definition["phrases"]
    definition.update(values)
    before = deepcopy(rules.snapshot())
    with pytest.raises(ValueError, match=error):
        await rules.async_create(definition)
    assert rules.snapshot() == before
    assert rules.match("hello").rule["id"] == "existing"


@pytest.mark.parametrize(
    "text,match_type,phrase,matching,expected_phrase,expected_slots,expected_fuzzy",
    [
        pytest.param(
            "parties",
            "equals",
            "party",
            None,
            "party",
            {},
            False,
            id="normalized-exact",
        ),
        pytest.param(
            "save milk",
            "sentence_pattern",
            ["remember {item}", "save {item}"],
            None,
            "save {item}",
            {"item": "milk"},
            False,
            id="later-sentence-variant",
        ),
        pytest.param(
            "liagt",
            "equals",
            "light",
            {"fuzzy": True, "fuzzy_threshold": 80},
            "light",
            {},
            True,
            id="fuzzy",
        ),
        pytest.param(
            "goodbye", "equals", "hello", None, None, {}, False, id="no-match"
        ),
    ],
)
@pytest.mark.parametrize("action_type", ["local_action", "model_routing"])
async def test_preview_matches_production_for_same_rule_and_request(
    hass,
    text,
    match_type,
    phrase,
    matching,
    expected_phrase,
    expected_slots,
    expected_fuzzy,
    action_type,
):
    definition = rule("command", phrase, match_type, matching=matching)
    if action_type == "model_routing":
        definition.update(
            action_type=action_type,
            action={"model": "gpt-5", "scope": "conversation", "continue_to_ai": True},
        )
    rules = await manager(definition)
    before = deepcopy(rules.snapshot())
    # Preview projects the same async evaluator used by production. It does not
    # execute an independent matcher or any of the selected rule's actions.
    match = await rules.async_match(hass, text)
    assert match == rules.match(text)
    preview = request_rule_match_preview(match)
    if expected_phrase is None:
        assert preview == {"matched": False}
    else:
        assert preview == {
            "matched": True,
            "rule": {
                "id": "command",
                "name": "command",
                "match_type": match_type,
                "action_type": action_type,
            },
            "matched_phrase": expected_phrase,
            "fuzzy": expected_fuzzy,
            "score": 80.0 if expected_fuzzy else 100.0,
            "captured_values": expected_slots,
            "would_do": (
                {
                    "type": "local_action",
                    "action_count": 1,
                    "consumed": True,
                    "provider_input": "none",
                }
                if action_type == "local_action"
                else {
                    "type": "model_routing",
                    "reset": False,
                    "model": "gpt-5",
                    "reasoning_effort": None,
                    "scope": "conversation",
                    "consumed": False,
                    "provider_input": "original",
                }
            ),
        }
    assert rules.snapshot() == before
    hass.services.async_call.assert_not_called()
