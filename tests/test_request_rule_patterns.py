"""Tests for the bounded ExtendedOpenAI Request Rule sentence matcher."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import request_rule_patterns as patterns
from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    MAX_MATCH_INPUT_CHARS,
    MAX_MATCH_INPUT_WORDS,
    MAX_PATTERN_CAPTURES,
    MAX_PATTERN_NESTING,
    CompiledSentencePattern,
    MatchBudget,
    SentenceMatchLimitError,
    SentencePatternError,
    _Alternative,
    _Compiler,
    _Fragment,
    _Literal,
    _Optional,
    _Parser,
    _State,
    _can_match_empty,
    _contains_adjacent_free_captures,
    _has_required_anchor,
    _required_fragments,
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
    assert pattern.match(" ".join(["a"] * 20 + ["b", "c"]), near_miss_budget) is None
    assert 0 < near_miss_budget.used < 1_000


def test_runtime_budget_is_enforceable() -> None:
    """Matcher work should stop itself rather than depend on an async timeout."""
    pattern = compile_sentence_pattern("[please ](turn|switch) {room} lights on")
    with pytest.raises(SentenceMatchLimitError, match="safe work limit"):
        pattern.match(
            "please switch upstairs guest room lights on", MatchBudget(maximum=2)
        )


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
    captures = " ".join(
        f"x{i} {{value{i}=a|b}}" for i in range(MAX_PATTERN_CAPTURES + 1)
    )
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


@pytest.mark.parametrize(
    "source",
    [
        "do [{first}] {second}",
        "do ({first}|x) {second}",
        "do {first} (x|[{second}] y)",
        "do [x {first}] {second}",
    ],
)
def test_grouped_adjacent_captures_are_rejected(source) -> None:
    with pytest.raises(SentencePatternError, match="separated"):
        compile_sentence_pattern(source)


@pytest.mark.parametrize(
    "source",
    [
        "do [{first} to] {second}",
        "do {first} [to {second}]",
        "do ({first} to|x) {second}",
        "do {first} {mode=x|y} {second}",
    ],
)
def test_grouped_captures_with_required_separators_are_valid(source) -> None:
    compile_sentence_pattern(source)


def test_escaped_constrained_values_do_not_create_extra_choices() -> None:
    pattern = compile_sentence_pattern(r"say {choice=a\|b|c\}d|e\\f|g\=h}")
    for text in ("a|b", "c}d", "e\\f", "g=h"):
        assert pattern.match(f"say {text}").captures == {"choice": text}
    for text in ("a", "b", "c", "e", "g"):
        assert pattern.match(f"say {text}") is None
    literal_range = compile_sentence_pattern(r"say {choice=0\.\.10|other}")
    assert literal_range.match("say 0..10").captures == {"choice": "0..10"}
    assert literal_range.match("say 5") is None


@pytest.mark.parametrize(
    ("source", "text"),
    [
        ("say ①", "say ①"),
        ("say cafe\u0301", "say café"),
        ("set {room=home  office|bedroom}", "set home office"),
        ("set {room=cafe\u0301|home}", "set café"),
        ("say ｛hello｝", "say {hello}"),
    ],
)
def test_pattern_tokens_and_input_share_normalization(source, text) -> None:
    assert compile_sentence_pattern(source).match(text) is not None


@pytest.mark.parametrize(
    "source",
    [
        "set {room=home office|home  office}",
        "set {room=cafe\u0301|café}",
        "set {value=①|1}",
    ],
)
def test_normalized_duplicate_choices_are_rejected(source) -> None:
    with pytest.raises(SentencePatternError, match="duplicate"):
        compile_sentence_pattern(source)


def test_sentence_ending_punctuation_is_tolerated_without_losing_literals() -> None:
    pattern = compile_sentence_pattern("turn lights on")
    for text in (
        "Turn lights on.",
        "turn lights on!",
        "turn lights on？！",
        "turn lights on。",
    ):
        assert pattern.match(text) is not None
    assert pattern.match("turn lights on. then off") is None
    assert compile_sentence_pattern(r"say hello\!").match("say hello") is None
    assert compile_sentence_pattern(r"say hello\!").match("say hello!") is not None
    assert compile_sentence_pattern("remember {fact}").match(
        "remember milk!"
    ).captures == {"fact": "milk"}


def test_normalized_input_expansion_is_bounded() -> None:
    pattern = compile_sentence_pattern("remember {fact}")
    with pytest.raises(SentenceMatchLimitError, match="characters"):
        pattern.match("remember " + "\ufdfa" * 150)
    with pytest.raises(SentenceMatchLimitError, match="characters"):
        pattern.match("remember " + "ß" * 1100)


def test_capture_heavy_near_miss_has_linear_state_work() -> None:
    pattern = compile_sentence_pattern("do {first} and {second} end")
    for count in (20, 40, 80):
        text = "do " + " and ".join(["x"] * count) + " end nope"
        budget = MatchBudget()
        assert pattern.match(text, budget) is None
        assert 0 < budget.used <= 2 * pattern.state_count * (len(text) + 1)
        assert budget.used < 10_000


def test_compile_cache_is_immutable_and_reused() -> None:
    from dataclasses import FrozenInstanceError

    first = compile_sentence_pattern("turn [the] light on")
    assert compile_sentence_pattern("turn [the] light on") is first
    with pytest.raises(FrozenInstanceError):
        first.states[0].out = 100


def test_compile_state_ceiling(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rule_patterns as module,
    )

    monkeypatch.setattr(module, "MAX_PATTERN_STATES", 4)
    compile_sentence_pattern.cache_clear()
    with pytest.raises(SentencePatternError, match="state complexity"):
        compile_sentence_pattern("unique [complex] {capture} pattern")


def test_numeric_captures_can_be_followed_by_literal_digits() -> None:
    assert compile_sentence_pattern("set {value=0..10}0").match("set 100").captures == {
        "value": "10"
    }
    assert compile_sentence_pattern("set {value=-10..-1}0").match(
        "set -100"
    ).captures == {"value": "-10"}
    assert compile_sentence_pattern("set {value=0..10}").match("set 100") is None
    assert compile_sentence_pattern("set {value=0..10}").match("set 0005").captures == {
        "value": "0005"
    }
    assert compile_sentence_pattern("set {value=0..10}").match("set +5").captures == {
        "value": "+5"
    }
    assert compile_sentence_pattern("set {value=0..10}").match("set ٥").captures == {
        "value": "٥"
    }


def test_captures_do_not_split_casefolded_display_characters() -> None:
    assert compile_sentence_pattern("say {value}s").match("say ß") is None
    assert compile_sentence_pattern("say s{value}").match("say ß") is None
    assert compile_sentence_pattern("say {value}").match("say ß").captures == {
        "value": "ß"
    }


def test_whitespace_capture_histories_do_not_hide_valid_capture_splits() -> None:
    pattern = compile_sentence_pattern("do {first} to{second}")
    assert pattern.match("do a to to b").captures == {"first": "a", "second": "to b"}


def test_validate_match_input_rejects_non_string() -> None:
    with pytest.raises(patterns.SentenceMatchLimitError, match="must be a string"):
        patterns.validate_match_input(123)  # type: ignore[arg-type]


def test_sentence_capture_names_returns_validated_names() -> None:
    assert patterns.sentence_capture_names(
        "move {item} to {room=kitchen|study}"
    ) == ("item", "room")


@pytest.mark.parametrize("source", ["", "   ", None])
def test_pattern_is_required(source: object) -> None:
    with pytest.raises(patterns.SentencePatternError, match="pattern is required"):
        patterns.compile_sentence_pattern(source)  # type: ignore[arg-type]


def test_pattern_length_limit_is_enforced() -> None:
    with pytest.raises(patterns.SentencePatternError, match="at most"):
        patterns.compile_sentence_pattern("x" * (patterns.MAX_PATTERN_CHARS + 1))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("say \\", "ends with an escape"),
        ("say [hello", "missing closing"),
        ("say (hello|)", "cannot be empty"),
        ("say ]", "unexpected"),
        ("say {", "missing closing"),
        ("say {}", "name is required"),
        ("say {1bad}", "must start with a letter"),
        ("say {item} and {item}", "used more than once"),
        ("say {item=}", "needs a constraint"),
        ("say {item=10..1}", "minimum greater"),
        ("say {item=a\\}", "missing closing"),
        ("say {item=a||b}", "empty choice"),
    ],
)
def test_parser_validation_edges(source: str, message: str) -> None:
    patterns.compile_sentence_pattern.cache_clear()
    with pytest.raises(patterns.SentencePatternError, match=message):
        patterns.compile_sentence_pattern(source)


def test_free_capture_limit_is_enforced() -> None:
    source = "do " + " x ".join(
        f"{{value{index}}}" for index in range(patterns.MAX_FREE_TEXT_CAPTURES + 1)
    )
    with pytest.raises(patterns.SentencePatternError, match="free-text captures"):
        patterns.compile_sentence_pattern(source)


def test_constrained_choice_limits_are_enforced() -> None:
    too_many = "|".join(
        f"v{index}" for index in range(patterns.MAX_CONSTRAINED_VALUES + 1)
    )
    with pytest.raises(patterns.SentencePatternError, match="supports at most"):
        patterns.compile_sentence_pattern(f"set {{value={too_many}}}")

    too_long = "x" * (patterns.MAX_CONSTRAINED_VALUE_CHARS + 1)
    with pytest.raises(patterns.SentencePatternError, match="may be at most"):
        patterns.compile_sentence_pattern(f"set {{value={too_long}|ok}}")


def test_single_branch_groups_compile_normally() -> None:
    grouped = patterns.compile_sentence_pattern("say (hello)")
    assert grouped.match("say hello") is not None
    optional = patterns.compile_sentence_pattern("say [please] hello")
    assert optional.match("say hello") is not None


def test_required_fragment_fast_rejection_and_numeric_nonmatch() -> None:
    compiled = patterns.compile_sentence_pattern("turn {room=kitchen|study} light on")
    prepared = patterns.prepare_match_text("switch kitchen fan on")
    assert compiled.could_match(prepared) is False
    assert compiled.match_prepared(prepared) is None

    numeric = patterns.compile_sentence_pattern("set {level=-2..2}")
    assert numeric.match("set nope") is None
    assert numeric.match("set -9") is None


def test_display_helpers_cover_empty_expansion_and_terminal_spans() -> None:
    empty = patterns.prepare_match_text("")
    assert patterns._display_span(empty, 0, 0) == (0, 0)

    prepared = patterns.prepare_match_text("straße")
    # ß expands to ss; the offset between those folded characters is not a
    # display boundary.
    assert patterns._display_boundary(prepared, 5) is False
    assert patterns._display_boundary(prepared, 0) is True
    assert patterns._display_boundary(prepared, len(prepared.folded)) is True
    assert patterns._display_span(prepared, len(prepared.folded), len(prepared.folded)) == (
        len(prepared.display),
        len(prepared.display),
    )


def test_compiler_defensive_guards_reject_invalid_internal_shapes() -> None:
    compiler = patterns._Compiler()
    state = compiler.add(patterns._State("literal", value="x"))
    with pytest.raises(patterns.SentencePatternError, match="invalid compiled patch field"):
        compiler.patch(((state, "bogus"),), 0)

    with pytest.raises(patterns.SentencePatternError, match="unsupported sentence-pattern expression"):
        compiler.compile(object())

    with pytest.raises(patterns.SentencePatternError, match="unsupported capture kind"):
        compiler._compile_capture(patterns._Capture("value", "bogus"))


def _compiled_with_states(
    *states: patterns._State,
    capture_names: tuple[str, ...] = (),
) -> patterns.CompiledSentencePattern:
    return patterns.CompiledSentencePattern(
        source="synthetic",
        states=states,
        start_state=0,
        capture_names=capture_names,
        required_fragments=(),
    )


def test_matcher_defensive_guards_reject_invalid_compiled_states() -> None:
    missing_out = _compiled_with_states(patterns._State("literal", value="x"))
    with pytest.raises(patterns.SentenceMatchLimitError, match="invalid compiled sentence state"):
        missing_out.match("x")

    unknown = _compiled_with_states(patterns._State("mystery", out=0))
    with pytest.raises(patterns.SentenceMatchLimitError, match="unknown compiled state"):
        unknown.match("")

    bad_capture = _compiled_with_states(
        patterns._State("capture_end", out=1, name="value"),
        patterns._State("match"),
        capture_names=("value",),
    )
    with pytest.raises(patterns.SentenceMatchLimitError, match="invalid compiled capture state"):
        bad_capture.match("x")


def test_split_with_only_preferred_branch_and_space_boundaries_are_safe() -> None:
    synthetic = _compiled_with_states(
        patterns._State("split", out1=1),
        patterns._State("space", out=2),
        patterns._State("match"),
    )
    assert synthetic.match("") is not None
    assert synthetic.match(" ") is not None


def test_match_split_with_only_second_branch() -> None:
    """A defensive split with only out2 populated must still be traversable."""
    compiled = CompiledSentencePattern(
        source="manual",
        states=(
            _State("split", out2=1),
            _State("literal", value="hello", out=2),
            _State("match"),
        ),
        start_state=0,
        capture_names=(),
        required_fragments=(),
    )

    match = compiled.match("hello")

    assert match is not None
    assert match.captures == {}


def test_parser_rejects_unconsumed_trailing_character(monkeypatch: pytest.MonkeyPatch) -> None:
    """parse() rejects a defensive partial parse that leaves source unconsumed."""
    parser = _Parser("abc")
    monkeypatch.setattr(parser, "_sequence", lambda _stops, _depth: _Literal("abc"))

    with pytest.raises(SentencePatternError, match=r"unexpected 'a' at position 1"):
        parser.parse()


def test_parser_drops_literal_normalized_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """flush() must not append an empty literal after normalization."""
    monkeypatch.setattr(patterns, "_normalize_literal", lambda _value: "")
    parser = _Parser("x")

    assert parser.parse() == _Literal("")


def test_parser_collapses_repeated_whitespace() -> None:
    """Mixed repeated whitespace is one logical sentence separator."""
    compiled = compile_sentence_pattern("turn \t  \n lights on")

    assert compiled.match("turn lights on") is not None
    assert compiled.match("turn     lights on") is not None


def test_group_rejects_unexpected_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    """_group() defends against a cursor on neither its close nor alternative token."""
    parser = _Parser("x")
    monkeypatch.setattr(parser, "_sequence", lambda _stops, _depth: _Literal("branch"))

    with pytest.raises(SentencePatternError, match=r"unexpected 'x' in group"):
        parser._group(")", 0)


def test_pattern_requiring_no_text_is_rejected() -> None:
    """An entirely optional pattern cannot become a match-everything rule."""
    with pytest.raises(SentencePatternError, match="must require some text"):
        compile_sentence_pattern("[please]")


@pytest.mark.parametrize(
    ("helper", "expected"),
    [
        (_has_required_anchor, False),
        (_can_match_empty, False),
        (_contains_adjacent_free_captures, False),
    ],
)
def test_expression_helpers_reject_unknown_node_type(helper, expected: bool) -> None:
    """Expression walkers fail closed for unsupported internal node shapes."""
    assert helper(object()) is expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (_Alternative(()), set()),
        (object(), set()),
    ],
)
def test_required_fragments_defensive_shapes(expression: object, expected: set[str]) -> None:
    assert _required_fragments(expression) == expected


def test_compiler_returns_single_alternative_fragment_directly() -> None:
    """A defensive one-item Alternative compiles without adding a split."""
    compiler = _Compiler()

    fragment = compiler.compile(_Alternative((_Literal("hello"),)))

    assert fragment == _Fragment(0, ((0, "out"),))
    assert compiler.states == [_State("literal", value="hello")]


def test_compiler_supports_empty_literal() -> None:
    """The compiler can represent the parser's defensive empty literal node."""
    compiler = _Compiler()

    fragment = compiler._compile_literal("")

    assert fragment == _Fragment(0, ((0, "out"),))
    assert compiler.states == [_State("literal", value="")]
