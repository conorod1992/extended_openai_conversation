"""Bounded parser and matcher for ExtendedOpenAI Request Rule sentence patterns."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


@dataclass(slots=True)
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

    def match(self, text: str, budget: MatchBudget | None = None) -> PatternMatch | None:
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
        work = budget or MatchBudget()
        capture_index = {name: index for index, name in enumerate(self.capture_names)}
        empty_starts = (-1,) * len(self.capture_names)
        empty_values: tuple[str | None, ...] = (None,) * len(self.capture_names)
        queue: deque[
            tuple[int, int, tuple[int, ...], tuple[str | None, ...]]
        ] = deque([(self.start_state, 0, empty_starts, empty_values)])
        seen: set[tuple[int, int, tuple[int, ...]]] = set()

        while queue:
            state_index, pos, starts, values = queue.popleft()
            key = (state_index, pos, starts)
            if key in seen:
                continue
            seen.add(key)
            work.consume()
            state = self.states[state_index]

            if state.kind == "match":
                if pos == len(folded):
                    return PatternMatch(
                        {
                            name: values[index] or ""
                            for name, index in capture_index.items()
                        }
                    )
                continue

            if state.kind == "split":
                # out1 is preferred. Optional/capture exits are compiled there so
                # omitted optionals and shortest viable free captures win ties.
                if state.out1 is not None:
                    queue.appendleft((state.out1, pos, starts, values))
                if state.out2 is not None:
                    queue.append((state.out2, pos, starts, values))
                continue

            if state.kind == "capture_start":
                assert state.name is not None and state.out is not None
                index = capture_index[state.name]
                updated = list(starts)
                updated[index] = pos
                queue.appendleft((state.out, pos, tuple(updated), values))
                continue

            if state.kind == "capture_end":
                assert state.name is not None and state.out is not None
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
                updated_values = list(values)
                updated_values[index] = captured
                queue.appendleft(
                    (state.out, pos, tuple(updated_starts), tuple(updated_values))
                )
                continue

            if state.out is None:
                raise SentenceMatchLimitError("invalid compiled sentence state")

            if state.kind == "literal":
                assert state.value is not None
                if folded.startswith(state.value, pos):
                    queue.append((state.out, pos + len(state.value), starts, values))
                continue

            if state.kind == "any":
                if pos < len(folded):
                    queue.append((state.out, pos + 1, starts, values))
                continue

            if state.kind == "enum":
                for choice in state.values:
                    work.consume()
                    if folded.startswith(choice, pos):
                        queue.append((state.out, pos + len(choice), starts, values))
                continue

            if state.kind == "number":
                match = _INTEGER_AT.match(folded, pos)
                if match is None:
                    continue
                number = int(match.group())
                assert state.minimum is not None and state.maximum is not None
                if state.minimum <= number <= state.maximum:
                    queue.append((state.out, match.end(), starts, values))
                continue

            raise SentenceMatchLimitError(f"unknown compiled state {state.kind}")

        return None


def validate_match_input(text: str) -> None:
    """Validate one live/Preview utterance before any sentence-pattern work."""
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
    """Return capture names without constructing the runtime automaton."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise SentencePatternError("sentence pattern is required")
    parser = _Parser(pattern.strip())
    expression = parser.parse()
    _validate_expression(expression)
    return tuple(parser.capture_names)


def compile_sentence_pattern(pattern: str) -> CompiledSentencePattern:
    """Parse and compile the documented ExtendedOpenAI sentence-pattern grammar."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise SentencePatternError("sentence pattern is required")
    parser = _Parser(pattern.strip())
    expression = parser.parse()
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


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0
        self.capture_names: list[str] = []
        self.free_capture_count = 0

    def parse(self) -> object:
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
                value = "".join(literal)
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
                body.append(char)
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
            raise SentencePatternError(f"captured value {name!r} is used more than once")
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

        values = tuple(item.strip() for item in spec.split("|"))
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
        return not expression.value
    if isinstance(expression, _Optional):
        return True
    if isinstance(expression, _Alternative):
        return any(_can_match_empty(item) for item in expression.items)
    if isinstance(expression, _Sequence):
        return all(_can_match_empty(item) for item in expression.items)
    return False


def _only_separator(expression: object) -> bool:
    if isinstance(expression, _Literal):
        return not expression.value.strip()
    if isinstance(expression, _Optional):
        return _only_separator(expression.item)
    if isinstance(expression, _Alternative):
        return all(_only_separator(item) for item in expression.items)
    if isinstance(expression, _Sequence):
        return all(_only_separator(item) for item in expression.items)
    return False


def _contains_adjacent_free_captures(expression: object) -> bool:
    if isinstance(expression, _Alternative):
        return any(_contains_adjacent_free_captures(item) for item in expression.items)
    if isinstance(expression, _Optional):
        return _contains_adjacent_free_captures(expression.item)
    if not isinstance(expression, _Sequence):
        return False
    if any(_contains_adjacent_free_captures(item) for item in expression.items):
        return True
    for left, item in enumerate(expression.items):
        if not isinstance(item, _Capture) or item.kind != "free":
            continue
        right = left + 1
        while right < len(expression.items) and _only_separator(expression.items[right]):
            right += 1
        if (
            right < len(expression.items)
            and isinstance(expression.items[right], _Capture)
            and expression.items[right].kind == "free"
        ):
            return True
    return False


def _required_fragments(expression: object) -> set[str]:
    if isinstance(expression, _Literal):
        value = expression.value.strip().casefold()
        return {value} if len(value) >= 2 else set()
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
            setattr(self.states[index], attribute, target)

    def compile(self, expression: object) -> _Fragment:
        if isinstance(expression, _Literal):
            state = self.add(_State("literal", value=expression.value.casefold()))
            return _Fragment(state, ((state, "out"),))
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
                current = _Fragment(split, fragment.outs + current.outs)
            return current
        if isinstance(expression, _Optional):
            fragment = self.compile(expression.item)
            split = self.add(_State("split", out1=fragment.start))
            return _Fragment(split, fragment.outs + ((split, "out2"),))
        if isinstance(expression, _Capture):
            start = self.add(_State("capture_start", name=expression.name))
            end = self.add(_State("capture_end", name=expression.name))
            if expression.kind == "free":
                any_state = self.add(_State("any"))
                split = self.add(_State("split", out1=end, out2=any_state))
                self.states[start].out = any_state
                self.states[any_state].out = split
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
            self.states[start].out = matcher
            self.states[matcher].out = end
            return _Fragment(start, ((end, "out"),))
        raise SentencePatternError("unsupported sentence-pattern expression")


def prepare_match_text(text: str) -> PreparedSentenceText:
    """Normalize one utterance once while retaining capture offsets."""
    validate_match_input(text)
    display = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).strip())
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
