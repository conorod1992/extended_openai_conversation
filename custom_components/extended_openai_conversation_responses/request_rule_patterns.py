"""Bounded parser and matcher for ExtendedOpenAI Request Rule sentence patterns."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from heapq import heappop, heappush
from itertools import count
import re
import unicodedata

MAX_MATCH_INPUT_CHARS = 2048
MAX_MATCH_INPUT_WORDS = 256
MAX_PATTERN_NESTING = 8
MAX_PATTERN_CAPTURES = 8
MAX_FREE_TEXT_CAPTURES = 4
MAX_CONSTRAINED_VALUES = 32
MAX_CONSTRAINED_VALUE_CHARS = 80
MAX_PATTERN_STATES = 512
MAX_AGENT_PATTERN_STATES = 50_000
MAX_MATCH_WORK = 200_000
MAX_PATTERN_CHARS = 200
_SENTENCE_END = frozenset(".!?。؟")  # NFKC folds fullwidth punctuation to ASCII.

_CAPTURE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_NUMERIC_RANGE = re.compile(r"^([+-]?\d+)\.\.([+-]?\d+)$")
_INTEGER_AT = re.compile(r"[+-]?\d+")


class SentencePatternError(ValueError):
    """Raised when an ExtendedOpenAI sentence pattern is invalid or unsupported."""


class SentenceMatchLimitError(RuntimeError):
    """Raised when bounded Request Rule matching cannot safely complete."""


@dataclass(frozen=True, slots=True)
class PatternMatch:
    """One complete sentence-pattern match."""

    captures: dict[str, str]


@dataclass(frozen=True, slots=True)
class _Literal:
    value: str


@dataclass(frozen=True, slots=True)
class _Sequence:
    items: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Alternative:
    items: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _Optional:
    item: object


@dataclass(frozen=True, slots=True)
class _Capture:
    name: str
    kind: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class _State:
    kind: str
    out: int | None = None
    out1: int | None = None
    out2: int | None = None
    value: str | None = None
    values: tuple[str, ...] = ()
    name: str | None = None
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class _Fragment:
    start: int
    outs: tuple[tuple[int, str], ...]


@dataclass(slots=True)
class MatchBudget:
    """Shared aggregate work budget across all sentence patterns for one utterance."""

    maximum: int = MAX_MATCH_WORK
    used: int = 0

    def consume(self, amount: int = 1) -> None:
        """Charge deterministic matcher work and reject an exhausted budget."""
        self.used += amount
        if self.used > self.maximum:
            raise SentenceMatchLimitError(
                "Request Rule sentence matching exceeded the safe work limit"
            )


@dataclass(frozen=True, slots=True)
class PreparedSentenceText:
    """Normalized text plus an offset map back to captured display text."""

    display: str
    folded: str
    folded_to_display: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompiledSentencePattern:
    """Compiled bounded automaton for one ExtendedOpenAI sentence pattern."""

    source: str
    states: tuple[_State, ...]
    start_state: int
    capture_names: tuple[str, ...]
    required_fragments: tuple[str, ...]

    @property
    def state_count(self) -> int:
        """Return the compiled automaton size."""
        return len(self.states)

    def could_match(self, prepared: PreparedSentenceText) -> bool:
        """Reject text missing a literal every successful path needs."""
        return all(fragment in prepared.folded for fragment in self.required_fragments)

    def match(
        self, text: str, budget: MatchBudget | None = None
    ) -> PatternMatch | None:
        """Match unprepared text with the same bounded runtime used by Request Rules."""
        return self.match_prepared(prepare_match_text(text), budget)

    def match_prepared(
        self, prepared: PreparedSentenceText, budget: MatchBudget | None = None
    ) -> PatternMatch | None:
        """Match prepared text with bounded NFA work and deterministic captures."""
        if not self.could_match(prepared):
            return None

        display = prepared.display
        folded = prepared.folded
        # Speech recognizers may append sentence-ending punctuation. Explicit
        # punctuation literals still have to match; only the remaining suffix
        # after a complete pattern is optional.
        sentence_end = len(folded)
        while sentence_end and folded[sentence_end - 1] in _SENTENCE_END:
            sentence_end -= 1
        nonspace = [0]
        for char in folded:
            nonspace.append(nonspace[-1] + (char != " "))
        work = budget or MatchBudget()
        capture_index = {name: index for index, name in enumerate(self.capture_names)}
        empty_starts = (-1,) * len(self.capture_names)
        empty_lengths = (-1,) * len(self.capture_names)
        empty_values: tuple[str | None, ...] = (None,) * len(self.capture_names)
        serial = count()
        queue: list[
            tuple[
                tuple[int, ...],
                int,
                int,
                int,
                int,
                tuple[int, ...],
                tuple[int, ...],
                tuple[str | None, ...],
            ]
        ] = []

        def push(
            state_index: int,
            pos: int,
            starts: tuple[int, ...],
            lengths: tuple[int, ...],
            values: tuple[str | None, ...],
            penalty: int,
        ) -> None:
            # Free-text captures are deterministic: prefer the shortest value for
            # the earliest capture, then the shortest value for the next capture.
            # The branch penalty keeps preferred NFA branches depth-first when no
            # capture length distinguishes them, avoiding combinatorial optional
            # exploration while preserving a hard shared work budget.
            capture_priority = tuple(
                lengths[index]
                if lengths[index] >= 0
                else max(0, pos - starts[index])
                if starts[index] >= 0
                else 0
                for index in range(len(self.capture_names))
            )
            heappush(
                queue,
                (
                    capture_priority,
                    penalty,
                    next(serial),
                    state_index,
                    pos,
                    starts,
                    lengths,
                    values,
                ),
            )

        push(
            self.start_state,
            0,
            empty_starts,
            empty_lengths,
            empty_values,
            0,
        )
        seen: set[tuple[int, int, bool]] = set()

        while queue:
            (
                _capture_priority,
                penalty,
                _serial,
                state_index,
                pos,
                starts,
                lengths,
                values,
            ) = heappop(queue)
            # At the same state/position every history has the same possible
            # suffixes. The heap visits the best capture-length prefix first;
            # future input extends the same active capture equally. Retain only
            # that dominant history, distinguishing whitespace-only captures
            # because they cannot yet take capture_end. This avoids enumerating
            # every possible starting offset of later free captures.
            has_capture_text = any(
                start >= 0 and nonspace[pos] > nonspace[start] for start in starts
            )
            key = (state_index, pos, has_capture_text)
            if key in seen:
                continue
            seen.add(key)
            work.consume()
            state = self.states[state_index]

            if state.kind == "match":
                if pos >= sentence_end:
                    return PatternMatch(
                        {
                            name: values[index] or ""
                            for name, index in capture_index.items()
                        }
                    )
                continue

            if state.kind == "split":
                # out1 is the compiler's preferred branch. out2 gets a small
                # priority penalty so equivalent optional paths do not all fan out
                # before the preferred path can finish.
                if state.out1 is not None:
                    push(state.out1, pos, starts, lengths, values, penalty)
                if state.out2 is not None:
                    push(state.out2, pos, starts, lengths, values, penalty + 1)
                continue

            if state.kind == "capture_start":
                assert state.name is not None and state.out is not None
                if not _display_boundary(prepared, pos):
                    continue
                index = capture_index[state.name]
                updated_starts = list(starts)
                updated_starts[index] = pos
                updated_lengths = list(lengths)
                updated_lengths[index] = -1
                push(
                    state.out,
                    pos,
                    tuple(updated_starts),
                    tuple(updated_lengths),
                    values,
                    penalty,
                )
                continue

            if state.kind == "capture_end":
                assert state.name is not None and state.out is not None
                if not _display_boundary(prepared, pos):
                    continue
                index = capture_index[state.name]
                start = starts[index]
                if start < 0:
                    raise SentenceMatchLimitError("invalid compiled capture state")
                display_start, display_end = _display_span(prepared, start, pos)
                captured = display[display_start:display_end].strip()
                if not captured:
                    continue
                updated_starts = list(starts)
                updated_starts[index] = -1
                updated_lengths = list(lengths)
                updated_lengths[index] = pos - start
                updated_values = list(values)
                updated_values[index] = captured
                push(
                    state.out,
                    pos,
                    tuple(updated_starts),
                    tuple(updated_lengths),
                    tuple(updated_values),
                    penalty,
                )
                continue

            if state.out is None:
                raise SentenceMatchLimitError("invalid compiled sentence state")

            if state.kind == "literal":
                assert state.value is not None
                if folded.startswith(state.value, pos):
                    push(
                        state.out,
                        pos + len(state.value),
                        starts,
                        lengths,
                        values,
                        penalty,
                    )
                continue

            if state.kind == "space":
                # Input whitespace is normalized to one ASCII space. A pattern
                # separator may share an already-consumed separator or disappear
                # at an input boundary. This makes natural `[optional] word`
                # syntax work without allowing required middle separators to vanish.
                if pos < len(folded) and folded[pos] == " ":
                    push(state.out, pos + 1, starts, lengths, values, penalty)
                if pos in {0, len(folded)} or (pos > 0 and folded[pos - 1] == " "):
                    push(state.out, pos, starts, lengths, values, penalty)
                continue

            if state.kind == "any":
                if pos < len(folded):
                    push(state.out, pos + 1, starts, lengths, values, penalty)
                continue

            if state.kind == "enum":
                for choice in state.values:
                    work.consume()
                    if folded.startswith(choice, pos):
                        push(
                            state.out,
                            pos + len(choice),
                            starts,
                            lengths,
                            values,
                            penalty,
                        )
                continue

            if state.kind == "number":
                match = _INTEGER_AT.match(folded, pos)
                if match is None:
                    continue
                assert state.minimum is not None and state.maximum is not None
                negative = folded[pos] == "-"
                digit_start = pos + (folded[pos] in "+-")
                magnitude = 0
                ceiling = -state.minimum if negative else state.maximum
                # Try valid integer prefixes so an explicit numeric suffix can
                # still match, e.g. {value=0..10}0 against 100. Account for every
                # digit and stop once further digits cannot return to the range.
                for end in range(digit_start, match.end()):
                    work.consume()
                    magnitude = magnitude * 10 + int(folded[end])
                    if magnitude > ceiling:
                        break
                    number = -magnitude if negative else magnitude
                    if state.minimum <= number <= state.maximum:
                        push(state.out, end + 1, starts, lengths, values, penalty)
                continue

            raise SentenceMatchLimitError(f"unknown compiled state {state.kind}")

        return None


def validate_match_input(text: str) -> None:
    """Validate one live/Preview utterance before any Request Rule work."""
    if not isinstance(text, str):
        raise SentenceMatchLimitError("Request Rule matching text must be a string")
    if len(text) > MAX_MATCH_INPUT_CHARS:
        raise SentenceMatchLimitError(
            f"Request Rule matching supports at most {MAX_MATCH_INPUT_CHARS} characters"
        )
    if len(text.split()) > MAX_MATCH_INPUT_WORDS:
        raise SentenceMatchLimitError(
            f"Request Rule matching supports at most {MAX_MATCH_INPUT_WORDS} words"
        )


def sentence_capture_names(pattern: str) -> tuple[str, ...]:
    """Return capture names while applying the same grammar validation as runtime."""
    parser, expression = _parse_pattern(pattern)
    _validate_expression(expression)
    return tuple(parser.capture_names)


@lru_cache(maxsize=2048)
def compile_sentence_pattern(pattern: str) -> CompiledSentencePattern:
    """Parse and compile the documented ExtendedOpenAI sentence-pattern grammar."""
    parser, expression = _parse_pattern(pattern)
    _validate_expression(expression)
    compiler = _Compiler()
    fragment = compiler.compile(expression)
    match_state = compiler.add(_State("match"))
    compiler.patch(fragment.outs, match_state)
    return CompiledSentencePattern(
        source=pattern,
        states=tuple(compiler.states),
        start_state=fragment.start,
        capture_names=tuple(parser.capture_names),
        required_fragments=tuple(
            sorted(_required_fragments(expression), key=len, reverse=True)
        ),
    )


def _parse_pattern(pattern: str) -> tuple[_Parser, object]:
    if not isinstance(pattern, str) or not pattern.strip():
        raise SentencePatternError("sentence pattern is required")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise SentencePatternError(
            f"sentence patterns support at most {MAX_PATTERN_CHARS} characters"
        )
    parser = _Parser(pattern.strip())
    return parser, parser.parse()


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0
        self.capture_names: list[str] = []
        self.free_capture_count = 0

    def parse(self) -> object:
        """Parse one complete pattern into the small supported expression tree."""
        expression = self._sequence(set(), 0)
        if self.pos != len(self.source):
            raise SentencePatternError(
                f"unexpected {self.source[self.pos]!r} at position {self.pos + 1}"
            )
        return expression

    def _sequence(self, stops: set[str], depth: int) -> object:
        if depth > MAX_PATTERN_NESTING:
            raise SentencePatternError(
                f"sentence pattern nesting exceeds {MAX_PATTERN_NESTING} levels"
            )
        items: list[object] = []
        literal: list[str] = []

        def flush() -> None:
            if literal:
                # Normalize literal tokens only after recognizing syntax. NFKC
                # can itself produce braces, brackets and other grammar marks.
                value = _normalize_literal("".join(literal))
                if value:
                    items.append(_Literal(value))
                literal.clear()

        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char in stops:
                break
            if char == "\\":
                self.pos += 1
                if self.pos >= len(self.source):
                    raise SentencePatternError("sentence pattern ends with an escape")
                literal.append(self.source[self.pos])
                self.pos += 1
                continue
            if char.isspace():
                if not literal or literal[-1] != " ":
                    literal.append(" ")
                self.pos += 1
                while self.pos < len(self.source) and self.source[self.pos].isspace():
                    self.pos += 1
                continue
            if char == "[":
                flush()
                self.pos += 1
                items.append(_Optional(self._group("]", depth + 1)))
                continue
            if char == "(":
                flush()
                self.pos += 1
                items.append(self._group(")", depth + 1))
                continue
            if char == "{":
                flush()
                items.append(self._capture())
                continue
            if char == ";":
                raise SentencePatternError(
                    "permutations using ';' are not supported; use explicit alternatives"
                )
            if char == "<":
                raise SentencePatternError(
                    "named expansion rules (<name>) are not supported in Request Rules"
                )
            if char in "])}|":
                raise SentencePatternError(
                    f"unexpected {char!r} at position {self.pos + 1}"
                )
            literal.append(char)
            self.pos += 1

        flush()
        if not items:
            return _Literal("")
        if len(items) == 1:
            return items[0]
        return _Sequence(tuple(items))

    def _group(self, closing: str, depth: int) -> object:
        branches: list[object] = []
        while True:
            branches.append(self._sequence({"|", closing}, depth))
            if self.pos >= len(self.source):
                raise SentencePatternError(f"missing closing {closing!r}")
            char = self.source[self.pos]
            if char == closing:
                self.pos += 1
                break
            if char != "|":
                raise SentencePatternError(f"unexpected {char!r} in group")
            self.pos += 1
        if len(branches) == 1:
            return branches[0]
        if any(_can_match_empty(branch) for branch in branches):
            raise SentencePatternError(
                "alternative choices cannot be empty; use [optional] syntax instead"
            )
        return _Alternative(tuple(branches))

    def _capture(self) -> _Capture:
        assert self.source[self.pos] == "{"
        self.pos += 1
        body: list[str] = []
        escaped = False
        while self.pos < len(self.source):
            char = self.source[self.pos]
            self.pos += 1
            if escaped:
                body.extend(("\\", char))
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "}":
                break
            body.append(char)
        else:
            raise SentencePatternError("missing closing '}' for captured value")

        raw = "".join(body).strip()
        if not raw:
            raise SentencePatternError("captured value name is required")
        name, separator, spec = raw.partition("=")
        name = name.strip()
        if not _CAPTURE_NAME.fullmatch(name):
            raise SentencePatternError(
                "captured value names must start with a letter or underscore and "
                "contain only letters, numbers, and underscores"
            )
        if name in self.capture_names:
            raise SentencePatternError(
                f"captured value {name!r} is used more than once"
            )
        self.capture_names.append(name)
        if len(self.capture_names) > MAX_PATTERN_CAPTURES:
            raise SentencePatternError(
                f"sentence patterns support at most {MAX_PATTERN_CAPTURES} captures"
            )

        if not separator:
            self.free_capture_count += 1
            if self.free_capture_count > MAX_FREE_TEXT_CAPTURES:
                raise SentencePatternError(
                    "sentence patterns support at most "
                    f"{MAX_FREE_TEXT_CAPTURES} free-text captures"
                )
            return _Capture(name, "free")

        spec = spec.strip()
        if not spec:
            raise SentencePatternError(f"captured value {name!r} needs a constraint")
        range_match = _NUMERIC_RANGE.fullmatch(spec)
        if range_match:
            minimum, maximum = map(int, range_match.groups())
            if minimum > maximum:
                raise SentencePatternError(
                    f"numeric capture {name!r} has a minimum greater than its maximum"
                )
            return _Capture(name, "number", minimum=minimum, maximum=maximum)

        choices: list[str] = []
        choice: list[str] = []
        escaped = False
        for char in spec:
            if escaped:
                choice.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "|":
                choices.append("".join(choice))
                choice = []
            else:
                choice.append(char)
        if escaped:
            raise SentencePatternError("captured value ends with an escape")
        choices.append("".join(choice))
        values = tuple(_normalize_literal(item).strip() for item in choices)
        if any(not item for item in values):
            raise SentencePatternError(f"captured value {name!r} has an empty choice")
        if len(values) > MAX_CONSTRAINED_VALUES:
            raise SentencePatternError(
                f"captured value {name!r} supports at most "
                f"{MAX_CONSTRAINED_VALUES} choices"
            )
        if any(len(item) > MAX_CONSTRAINED_VALUE_CHARS for item in values):
            raise SentencePatternError(
                "captured value choices may be at most "
                f"{MAX_CONSTRAINED_VALUE_CHARS} characters"
            )
        folded = [item.casefold() for item in values]
        if len(set(folded)) != len(folded):
            raise SentencePatternError(
                f"captured value {name!r} contains duplicate choices"
            )
        return _Capture(name, "enum", values=values)


def _validate_expression(expression: object) -> None:
    if _can_match_empty(expression):
        raise SentencePatternError("sentence pattern must require some text")
    if not _has_required_anchor(expression):
        raise SentencePatternError(
            "sentence pattern must include required literal, constrained, or numeric text"
        )
    if _contains_adjacent_free_captures(expression):
        raise SentencePatternError(
            "free-text captures must be separated by required literal or constrained text"
        )


def _has_required_anchor(expression: object) -> bool:
    if isinstance(expression, _Literal):
        return bool(expression.value.strip())
    if isinstance(expression, _Capture):
        return expression.kind in {"enum", "number"}
    if isinstance(expression, _Optional):
        return False
    if isinstance(expression, _Sequence):
        return any(_has_required_anchor(item) for item in expression.items)
    if isinstance(expression, _Alternative):
        return all(_has_required_anchor(item) for item in expression.items)
    return False


def _can_match_empty(expression: object) -> bool:
    if isinstance(expression, _Literal):
        return not expression.value.strip()
    if isinstance(expression, _Capture):
        return False
    if isinstance(expression, _Optional):
        return True
    if isinstance(expression, _Alternative):
        return any(_can_match_empty(item) for item in expression.items)
    if isinstance(expression, _Sequence):
        return all(_can_match_empty(item) for item in expression.items)
    return False


def _contains_adjacent_free_captures(expression: object) -> bool:
    def visit(item: object, pending: set[bool]) -> tuple[set[bool], bool]:
        if isinstance(item, _Capture) and item.kind == "free":
            return {True}, True in pending
        if isinstance(item, _Literal):
            return ({False} if item.value.strip() else pending), False
        if isinstance(item, _Capture):
            return {False}, False
        if isinstance(item, _Optional):
            after, invalid = visit(item.item, pending)
            return pending | after, invalid
        if isinstance(item, _Alternative):
            after = set()
            invalid = False
            for branch in item.items:
                ends, bad = visit(branch, pending)
                after.update(ends)
                invalid |= bad
            return after, invalid
        if isinstance(item, _Sequence):
            invalid = False
            for child in item.items:
                pending, bad = visit(child, pending)
                invalid |= bad
            return pending, invalid
        return pending, False

    return visit(expression, {False})[1]


def _required_fragments(expression: object) -> set[str]:
    if isinstance(expression, _Literal):
        value = expression.value.strip().casefold()
        return {value} if value else set()
    if isinstance(expression, (_Optional, _Capture)):
        return set()
    if isinstance(expression, _Sequence):
        result: set[str] = set()
        for item in expression.items:
            result.update(_required_fragments(item))
        return result
    if isinstance(expression, _Alternative):
        if not expression.items:
            return set()
        common = _required_fragments(expression.items[0])
        for item in expression.items[1:]:
            common.intersection_update(_required_fragments(item))
        return common
    return set()


class _Compiler:
    def __init__(self) -> None:
        self.states: list[_State] = []

    def add(self, state: _State) -> int:
        self.states.append(state)
        if len(self.states) > MAX_PATTERN_STATES:
            raise SentencePatternError(
                f"sentence pattern exceeds the {MAX_PATTERN_STATES}-state complexity limit"
            )
        return len(self.states) - 1

    def patch(self, outs: tuple[tuple[int, str], ...], target: int) -> None:
        for index, attribute in outs:
            state = self.states[index]
            if attribute == "out":
                self.states[index] = replace(state, out=target)
            elif attribute == "out2":
                self.states[index] = replace(state, out2=target)
            else:
                raise SentencePatternError(f"invalid compiled patch field {attribute}")

    def compile(self, expression: object) -> _Fragment:
        if isinstance(expression, _Literal):
            return self._compile_literal(expression.value.casefold())
        if isinstance(expression, _Sequence):
            fragment = self.compile(expression.items[0])
            for item in expression.items[1:]:
                next_fragment = self.compile(item)
                self.patch(fragment.outs, next_fragment.start)
                fragment = _Fragment(fragment.start, next_fragment.outs)
            return fragment
        if isinstance(expression, _Alternative):
            fragments = [self.compile(item) for item in expression.items]
            if len(fragments) == 1:
                return fragments[0]
            current = fragments[-1]
            for fragment in reversed(fragments[:-1]):
                split = self.add(
                    _State("split", out1=fragment.start, out2=current.start)
                )
                current = _Fragment(split, (*fragment.outs, *current.outs))
            return current
        if isinstance(expression, _Optional):
            fragment = self.compile(expression.item)
            split = self.add(_State("split", out1=fragment.start))
            return _Fragment(split, (*fragment.outs, (split, "out2")))
        if isinstance(expression, _Capture):
            return self._compile_capture(expression)
        raise SentencePatternError("unsupported sentence-pattern expression")

    def _compile_literal(self, value: str) -> _Fragment:
        """Compile literal text, keeping separators as independently mergeable states."""
        parts = [part for part in re.split(r"( )", value) if part]
        if not parts:
            state = self.add(_State("literal", value=""))
            return _Fragment(state, ((state, "out"),))

        first: int | None = None
        previous: int | None = None
        for part in parts:
            state = self.add(
                _State("space") if part == " " else _State("literal", value=part)
            )
            if first is None:
                first = state
            if previous is not None:
                self.states[previous] = replace(self.states[previous], out=state)
            previous = state
        assert first is not None and previous is not None
        return _Fragment(first, ((previous, "out"),))

    def _compile_capture(self, expression: _Capture) -> _Fragment:
        start = self.add(_State("capture_start", name=expression.name))
        end = self.add(_State("capture_end", name=expression.name))
        if expression.kind == "free":
            any_state = self.add(_State("any"))
            split = self.add(_State("split", out1=end, out2=any_state))
            self.states[start] = replace(self.states[start], out=any_state)
            self.states[any_state] = replace(self.states[any_state], out=split)
            return _Fragment(start, ((end, "out"),))
        if expression.kind == "enum":
            matcher = self.add(
                _State(
                    "enum",
                    values=tuple(value.casefold() for value in expression.values),
                )
            )
        elif expression.kind == "number":
            matcher = self.add(
                _State(
                    "number",
                    minimum=expression.minimum,
                    maximum=expression.maximum,
                )
            )
        else:
            raise SentencePatternError(f"unsupported capture kind {expression.kind}")
        self.states[start] = replace(self.states[start], out=matcher)
        self.states[matcher] = replace(self.states[matcher], out=end)
        return _Fragment(start, ((end, "out"),))


def _normalize_literal(text: str) -> str:
    """Normalize literal spelling without interpreting it as pattern syntax."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))


def prepare_match_text(text: str) -> PreparedSentenceText:
    """Normalize one utterance once while retaining capture offsets."""
    validate_match_input(text)
    display = _normalize_literal(text).strip()
    # Compatibility characters and case folding can expand the supplied text.
    validate_match_input(display)
    validate_match_input(display.casefold())
    folded_parts: list[str] = []
    folded_to_display: list[int] = []
    for index, char in enumerate(display):
        folded = char.casefold()
        folded_parts.append(folded)
        folded_to_display.extend([index] * len(folded))
    return PreparedSentenceText(
        display=display,
        folded="".join(folded_parts),
        folded_to_display=tuple(folded_to_display),
    )


def _display_boundary(prepared: PreparedSentenceText, pos: int) -> bool:
    """Captures cannot split a character expanded by case folding (e.g. ß)."""
    mapping = prepared.folded_to_display
    return pos in {0, len(mapping)} or mapping[pos - 1] != mapping[pos]


def _display_span(
    prepared: PreparedSentenceText, start: int, end: int
) -> tuple[int, int]:
    mapping = prepared.folded_to_display
    if not mapping:
        return (0, 0)
    display_start = mapping[start] if start < len(mapping) else len(prepared.display)
    if end <= start:
        return (display_start, display_start)
    display_end = (
        mapping[end - 1] + 1 if end - 1 < len(mapping) else len(prepared.display)
    )
    return (display_start, display_end)
