"""Residual branch coverage for Request Rule sentence patterns."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rule_patterns as patterns,
)


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
        ("say {item=a\\}", "ends with an escape"),
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
