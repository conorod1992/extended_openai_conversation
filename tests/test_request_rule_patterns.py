"""Tests for the bounded ExtendedOpenAI Request Rule sentence matcher."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    MAX_MATCH_INPUT_CHARS,
    MAX_MATCH_INPUT_WORDS,
    MAX_PATTERN_CAPTURES,
    MAX_PATTERN_NESTING,
    MatchBudget,
    SentenceMatchLimitError,
    SentencePatternError,
    compile_sentence_pattern,
    validate_match_input,
)


def test_optional_alternative_and_embedded_optional() -> None:
    """Documented optional and alternative syntax should compose safely."""
    pattern = compile_sentence_pattern(
        "[please ](turn|switch) {room=kitchen|bedroom} light[s] on"
    )

    first = pattern.match("please switch bedroom lights on")
    assert first is not None
    assert first.captures == {"room": "bedroom"}

    second = pattern.match("turn kitchen light on")
    assert second is not None
    assert second.captures == {"room": "kitchen"}

    assert pattern.match("turn office light on") is None


def test_free_text_captures_are_multiword_and_deterministic() -> None:
    """Free captures should choose the shortest split that permits a full match."""
    pattern = compile_sentence_pattern("add {item} to {list_name}")
    result = pattern.match("add semi skimmed milk to weekly shopping")

    assert result is not None
    assert result.captures == {
        "item": "semi skimmed milk",
        "list_name": "weekly shopping",
    }

    repeated_separator = pattern.match("add note to self to reminders")
    assert repeated_separator is not None
    assert repeated_separator.captures == {
        "item": "note",
        "list_name": "self to reminders",
    }


def test_constrained_and_numeric_captures() -> None:
    """Constrained values and integer ranges should validate while capturing."""
    room = compile_sentence_pattern("set {room=kitchen|bedroom|home office} mode")
    matched_room = room.match("set Home Office mode")
    assert matched_room is not None
    assert matched_room.captures == {"room": "Home Office"}
    assert room.match("set garage mode") is None

    level = compile_sentence_pattern("set brightness to {level=0..100}")
    matched_level = level.match("set brightness to 73")
    assert matched_level is not None
    assert matched_level.captures == {"level": "73"}
    assert level.match("set brightness to 173") is None

    signed = compile_sentence_pattern("set offset to {amount=-10..10}")
    matched_signed = signed.match("set offset to -5")
    assert matched_signed is not None
    assert matched_signed.captures == {"amount": "-5"}


def test_repeated_optionals_have_bounded_work() -> None:
    """The Hassil exponential repro should remain small in the bounded matcher."""
    pattern = compile_sentence_pattern(" ".join(["[a]"] * 20 + ["b"]))
    budget = MatchBudget(maximum=1_000)

    assert pattern.match(" ".join(["a"] * 20 + ["b"]), budget) is not None
    assert budget.used < 500

    near_miss_budget = MatchBudget(maximum=1_000)
    assert pattern.match(" ".join(["a"] * 20 + ["c"]), near_miss_budget) is None
    assert near_miss_budget.used < 500


def test_runtime_budget_is_enforceable() -> None:
    """Matcher work should stop itself rather than depend on an async timeout."""
    pattern = compile_sentence_pattern("[please ](turn|switch) {room} lights on")
    with pytest.raises(SentenceMatchLimitError, match="safe work limit"):
        pattern.match("please switch upstairs guest room lights on", MatchBudget(maximum=2))


def test_input_limits_are_explicit() -> None:
    """Live and Preview matching share bounded input limits."""
    validate_match_input("x" * MAX_MATCH_INPUT_CHARS)
    with pytest.raises(SentenceMatchLimitError, match="characters"):
        validate_match_input("x" * (MAX_MATCH_INPUT_CHARS + 1))

    validate_match_input(" ".join(["x"] * MAX_MATCH_INPUT_WORDS))
    with pytest.raises(SentenceMatchLimitError, match="words"):
        validate_match_input(" ".join(["x"] * (MAX_MATCH_INPUT_WORDS + 1)))


def test_ambiguous_or_unanchored_patterns_are_rejected() -> None:
    """Patterns that cannot assign captures predictably should fail at save time."""
    with pytest.raises(SentencePatternError, match="separated"):
        compile_sentence_pattern("do {first} {second}")
    with pytest.raises(SentencePatternError, match="required literal"):
        compile_sentence_pattern("{anything}")


def test_unsupported_hassil_specific_syntax_is_rejected() -> None:
    """The public grammar is ExtendedOpenAI's, not implicit Hassil compatibility."""
    with pytest.raises(SentencePatternError, match="named expansion"):
        compile_sentence_pattern("turn <device> on")
    with pytest.raises(SentencePatternError, match="permutations"):
        compile_sentence_pattern("(on; downstairs)")


def test_capture_and_nesting_limits_are_enforced() -> None:
    """Compile-time grammar bounds should be actionable instead of timing out later."""
    captures = " ".join(f"x{i} {{value{i}=a|b}}" for i in range(MAX_PATTERN_CAPTURES + 1))
    with pytest.raises(SentencePatternError, match="captures"):
        compile_sentence_pattern(captures)

    nested = "x"
    for _ in range(MAX_PATTERN_NESTING + 1):
        nested = f"[{nested}]"
    with pytest.raises(SentencePatternError, match="nesting"):
        compile_sentence_pattern(f"command {nested}")


def test_escaped_syntax_characters_are_literal() -> None:
    """Reserved pattern characters can still be spoken literally when escaped."""
    pattern = compile_sentence_pattern(r"say \(hello\) and \{goodbye\}")
    assert pattern.match("say (hello) and {goodbye}") is not None
