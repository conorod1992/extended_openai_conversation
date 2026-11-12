"""Terminal punctuation has a logical boundary; original AI suffixes survive."""

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    compile_sentence_pattern,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RuleMatch,
    rule_provider_input,
)


@pytest.mark.parametrize(
    "suffix", [" ?", "\u202f!", " ? !", "\u202f؟ 。", " \uff1f \uff01", " . ? !  "]
)
@pytest.mark.parametrize(
    "pattern,text,captures",
    [
        ("turn lights on", "Turn lights on", {}),
        ("turn {state=on|off}", "turn On", {"state": "On"}),
        ("set {level=1..10}", "set 5", {"level": "5"}),
        ("ask {query}", "ask Weather, tomorrow", {"query": "Weather, tomorrow"}),
        ("turn lights on [please]", "turn lights on", {}),
    ],
)
def test_spaced_suffix_matches_literal_constrained_and_free_endings(
    suffix, pattern, text, captures
):
    assert compile_sentence_pattern(pattern).match(text + suffix).captures == captures


@pytest.mark.parametrize(
    "suffix", [" ? !  ", "\u202f؟ 。", " \uff1f \uff01", " \u2026  ", " \u2048 !"]
)
@pytest.mark.parametrize(
    "capture_text", ["Weather tomorrow", "Weather  tomorrow", "Weather\u202ftomorrow"]
)
def test_terminal_handoff_preserves_exact_original_suffix(suffix, capture_text):
    text = "ask " + capture_text + suffix
    pattern = "ask {query}"
    slots = compile_sentence_pattern(pattern).match(text).captures
    match = RuleMatch(
        {"ai_input_mode": "capture", "ai_input_capture": "query"},
        pattern,
        False,
        100,
        slots,
    )
    assert rule_provider_input(match, text) == "Weather tomorrow" + suffix


@pytest.mark.parametrize(
    "pattern,text",
    [
        ("say hello!", "say hello ?"),
        ("add {item}", "add ? !"),
        ("add {item} to {list_name}", "add milk to? !"),
        ("ask {query}", "ask?"),
        ("turn lights on", "turn lights on ? then off"),
    ],
)
def test_existing_negative_boundaries(pattern, text):
    # A punctuation-only capture remains valid only with its required separator.
    if text == "add ? !":
        assert compile_sentence_pattern(pattern).match(text).captures == {"item": "?"}
    else:
        assert compile_sentence_pattern(pattern).match(text) is None


def test_nonterminal_capture_and_explicit_literals_keep_their_boundaries():
    pattern = "ask {query} please"
    text = "ask Weather? tomorrow please ? !"
    slots = compile_sentence_pattern(pattern).match(text).captures
    match = RuleMatch(
        {"ai_input_mode": "capture", "ai_input_capture": "query"},
        pattern,
        False,
        100,
        slots,
    )
    assert rule_provider_input(match, text) == "Weather? tomorrow"
    assert compile_sentence_pattern("say hello!").match("say hello! ? !")
    assert compile_sentence_pattern("add {item}").match("add .").captures == {
        "item": "."
    }
