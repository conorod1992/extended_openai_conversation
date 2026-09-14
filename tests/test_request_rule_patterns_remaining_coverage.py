"""Residual coverage for Request Rule sentence-pattern parser/compiler branches."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import request_rule_patterns as patterns
from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    CompiledSentencePattern,
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
)


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
