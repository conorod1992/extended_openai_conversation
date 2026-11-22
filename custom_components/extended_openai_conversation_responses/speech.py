"""Spoken-response post-processing that preserves the original assistant text."""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
import logging
import re
from string import punctuation
from typing import Any, Literal
import unicodedata
from urllib.parse import urlsplit

from .const import (
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_SPEECH_PROCESSING_ENABLED,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
)

_LOGGER = logging.getLogger(__name__)

_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


type _LiteralKind = Literal["code", "block_code", "escaped"]


@dataclass(frozen=True, slots=True)
class _LiteralSpan:
    """Half-open output range that later formatting must preserve."""

    start: int
    end: int
    kind: _LiteralKind


class _SpeechText(str):
    """Text plus literal spans for rendering and whole-response substitutions."""

    spans: tuple[_LiteralSpan, ...]

    def __new__(cls, text: str, spans: tuple[_LiteralSpan, ...]) -> _SpeechText:
        instance = super().__new__(cls, text)
        instance.spans = spans
        return instance


class _RenderedLabel(_SpeechText):
    """A label's formatted inline children, opaque to outer Markdown."""


def _join_speech(parts: list[str], kinds: Mapping[int, _LiteralKind]) -> str:
    """Combine formatted pieces while retaining and coalescing literal spans."""
    value = "".join(parts)
    if not kinds and not any(isinstance(part, _SpeechText) for part in parts):
        return value
    spans: list[_LiteralSpan] = []
    offset = 0

    def append_span(start: int, end: int, kind: _LiteralKind) -> None:
        if spans and spans[-1].kind == kind and spans[-1].end == start:
            spans[-1] = _LiteralSpan(spans[-1].start, end, kind)
        else:
            spans.append(_LiteralSpan(start, end, kind))

    for index, part in enumerate(parts):
        kind = kinds.get(index)
        if kind and part:
            append_span(offset, offset + len(part), kind)
        elif isinstance(part, _SpeechText):
            for span in part.spans:
                append_span(offset + span.start, offset + span.end, span.kind)
        offset += len(part)
    return _SpeechText(value, tuple(spans)) if spans else value


_BACKTICK_RUN = re.compile(r"`+")
_LABEL_SYNTAX = re.compile(r"`+|[\[\]]")
_LINK_WHITESPACE = " \t\r\n"
# Bare destinations stop at any whitespace, but only these separators can
# start or precede a Markdown destination or title.
_LINK_WHITESPACE_RUN = re.compile(rf"(?P<link>[{re.escape(_LINK_WHITESPACE)}]+)|\s+")
_LINK_TITLE_DELIMITERS = ('"', "'", "(")
_LEGACY_LINK_PROBE_PREFIX = 2048


def _link_whitespace_end(text: str, start: int, end: int) -> int:
    """Consume a link separator, rejecting a blank line even with indentation."""
    line_ended = False
    while start < end and text[start] in _LINK_WHITESPACE:
        if text[start] in "\r\n":
            if line_ended:
                return -1
            line_ended = True
            if text[start] == "\r" and start + 1 < end and text[start + 1] == "\n":
                start += 1
        start += 1
    return start


_URL_PREFIXES = ("http://", "https://", "www.")
_URL_TRAILING_PUNCTUATION = frozenset(".,;:!?")
_URL_PROSE_DASHES = "\u2014\u2013"
_URL_CJK_PROSE_PUNCTUATION = "\uff0c\u3002\uff01\uff1f\u3001\uff1b\uff1a"
# On a bare path, an em dash followed by a word and whitespace is
# typographical prose evidence. A naked suffix without that boundary stays
# in the URL, as do internal en dashes and URL syntax in the suffix.
_URL_PROSE_WORD_LIMIT = 24
_URL_PROSE_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*")
_URL_USERINFO = re.compile(r"[^/?#@]*@")
type _BareUrlPhase = Literal["authority", "long_userinfo", "host", "path", "query"]
_FORMAT_MARKERS = ("**", "__", "~~")
_URL_MARKDOWN_WRAPPERS = (
    ("**_", "_**"),
    ("__*", "*__"),
    ("_**", "**_"),
    ("*__", "__*"),
    ("*_", "_*"),
    ("_*", "*_"),
    ("***", "***"),
    ("___", "___"),
    ("**", "**"),
    ("__", "__"),
    ("~~", "~~"),
    ("*", "*"),
    ("_", "_"),
)
_URL_QUOTE_WRAPPERS = (('"', '"'), ("'", "'"), ("“", "”"), ("\u2018", "\u2019"))
_URL_WRAPPERS = (
    *_URL_QUOTE_WRAPPERS,
    *_URL_MARKDOWN_WRAPPERS,
    *(
        pair
        for opening, closing in _URL_QUOTE_WRAPPERS
        for opener, closer in _URL_MARKDOWN_WRAPPERS
        for pair in (
            (opening + opener, closer + closing),
            (opener + opening, closing + closer),
        )
    ),
)
_URL_WRAPPERS_BY_INITIAL = {
    initial: tuple(pair for pair in _URL_WRAPPERS if pair[0][0] == initial)
    for initial in {pair[0][0] for pair in _URL_WRAPPERS}
}
_URL_QUOTES_BY_INITIAL = {pair[0]: (pair,) for pair in _URL_QUOTE_WRAPPERS}
_URL_OPENER_BY_CLOSER = {closer: opener for opener, closer in _URL_WRAPPERS}
_URL_QUOTE_MAP = dict(_URL_QUOTE_WRAPPERS)
_URL_QUOTE_CLOSERS = frozenset(_URL_QUOTE_MAP.values())
_EMPHASIS_TOKENS = {"*": re.compile(r"`+|\*"), "_": re.compile(r"`+|_")}
# A *viable prefix*, not merely a failed complete match, may suspend
# progressive speech. This includes split closing asterisks.
_NUMERIC_EMPHASIS_PREFIX = re.compile(r"[+-]?(?:\d+(?:\.\d*)?)?\*{0,2}\Z")
_NUMERIC_EMPHASIS_COMPLETE = re.compile(r"[+-]?\d+(?:\.\d+)?\*\*(?=$|[\s.,;:!?])")

# A dotted label alone cannot distinguish a filename/product from a hostname.
# Use this address shape only together with the link's destination host.
_ADDRESS_LINK_LABEL = re.compile(
    r"(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s]*)?",
    re.IGNORECASE,
)

DEFAULT_STREAMING_BUFFER_LIMIT = 4096
COMPLETED_SPEECH_EXECUTOR_THRESHOLD = 4096
_FORMAT_PREFIX_LIMIT = 32
_QUOTE_MARKER = re.compile(r" {0,3}>[ \t]?")
_QUOTE_PREFIX = re.compile(r"^(?: {0,3}>[ \t]?)+")
_LIST_PREFIX = re.compile(r"[ ]{0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]+")


def _host_label_base(char: str) -> bool:
    return char.isalpha() or char.isdecimal()


def _looks_like_www_address(text: str, start: int, end: int | None = None) -> bool:
    """Check for two domain labels after an already recognized www. prefix."""
    first = start + len("www.")
    end = len(text) if end is None else end
    if first >= end or not _host_label_base(text[first]):
        return False
    for index in range(first + 1, end):
        char = text[index]
        if char == ".":
            return (
                text[index - 1] != "-"
                and index + 1 < end
                and _host_label_base(text[index + 1])
            )
        if _host_label_base(char) or char == "-":
            continue
        if text[index - 1] == "-" or not unicodedata.category(char).startswith("M"):
            return False
    return False


class _InlineScope:
    """Bounded lexical evidence for URL-adjacent closers, independent of chunks.

    This does not render Markdown. The existing formatter remains authoritative;
    this tracker only keeps real flanking/escape/code context for removed spans.
    """

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown
        self.previous = ""
        self.backslashes = 0
        self.code = 0
        self.block_code = False
        self.opened: list[str] = []
        self.overflow = False
        self.run = ""
        self.width = 0
        self.before = ""
        self.escaped = False
        self.numeric: str | None = None

    def _push(self, marker: str) -> None:
        if len(self.opened) >= 32 or (
            marker in _URL_QUOTE_CLOSERS and self.opened.count(marker) >= 16
        ):
            self.opened.clear()
            self.overflow = True
        elif not self.overflow:
            self.opened.append(marker)

    def _finish_run(self, after: str) -> None:
        char, width = self.run, self.width
        self.run, self.width = "", 0
        if not char or (self.escaped and not self.code):
            return
        if char == "`":
            self.code = 0 if self.code == width else (self.code or width)
            return
        if self.code or self.overflow or width > 3:
            return
        marker = char * width
        if marker not in {"*", "_", "**", "__", "***", "___", "~~"}:
            return
        if marker == "**" and self.numeric is not None:
            self.numeric = None
            return
        closing = ""
        for count, opened in enumerate(reversed(self.opened), 1):
            closing += opened
            if closing == marker and self.before and not self.before.isspace():
                del self.opened[-count:]
                return
            if len(closing) >= width:
                break
        if (
            marker == "**"
            and (self.before.isalnum() or self.before in {")", "]"})
            and (after.isdigit() or after in {"+", "-", "("})
        ):
            self.numeric = ""
            return
        if (
            after
            and not after.isspace()
            and (width > 1 or (not self.before.isalnum() and self.before != char))
        ):
            self._push(marker)

    def feed(self, text: str) -> None:
        if self.block_code:
            self.previous = text[-1:] or self.previous
            return
        for char in text:
            if self.run and char != self.run:
                self._finish_run(char)
            if self.markdown and char in "`*_~":
                if not self.run:
                    self.run = char
                    self.before = self.previous
                    self.escaped = bool(self.backslashes % 2)
                self.width += 1
            elif not self.code:
                if self.numeric is not None:
                    if char in "+-0123456789." and len(self.numeric) < 32:
                        self.numeric += char
                    else:
                        self.numeric = None
                if char == "\n" and self.overflow:
                    self.overflow = False
                if not self.overflow and not self.backslashes % 2:
                    if (
                        self.opened
                        and char == self.opened[-1]
                        and char in _URL_QUOTE_CLOSERS
                    ):
                        if self.previous and not self.previous.isspace():
                            self.opened.pop()
                    elif char in _URL_QUOTE_MAP and not self.previous.isalnum():
                        self._push(_URL_QUOTE_MAP[char])
            self.backslashes = self.backslashes + 1 if char == "\\" else 0
            self.previous = char

    def block_boundary(self, opening: bool) -> None:
        self.run, self.width = "", 0
        self.code = self.backslashes = 0
        self.block_code = opening

    def url_suffix(self, text: str) -> int:
        self._finish_run("h")
        self.numeric = None
        if self.code or self.overflow:
            return 0
        closer, result = "", 0
        for marker in reversed(self.opened):
            closer += marker
            if text.endswith(closer):
                boundary = len(text) - len(closer)
                if (
                    not StreamingSpeechSanitizer._is_escaped(text, boundary)
                    and text[boundary - 1 : boundary] != closer[:1]
                ):
                    result = len(closer)
        return result

    def removed(self) -> None:
        self._finish_run("x")
        self.numeric = None
        self.previous, self.backslashes = "x", 0

    def discard(self, opener: str) -> None:
        combined = ""
        for count, marker in enumerate(reversed(self.opened), 1):
            combined = marker + combined
            if combined == opener:
                del self.opened[-count:]
                return
            if len(combined) >= len(opener):
                return


@dataclass
class _LongDestination:
    close: str
    markdown: bool
    outer_parenthesis: bool
    depth: int = 0
    escaped: bool = False


class StreamingSpeechSanitizer:
    """Incrementally remove Markdown links, bare URLs, and speech formatting.

    Normal prose is released immediately. Only an unresolved construct, trailing
    whitespace, or short line-start prefix is retained. Malformed constructs are
    released verbatim after ``max_buffer_chars`` so provider output cannot make the
    TTS stream wait without bound.
    """

    def __init__(
        self,
        *,
        markdown: bool = True,
        urls: bool = True,
        max_buffer_chars: int = DEFAULT_STREAMING_BUFFER_LIMIT,
        inline: bool = False,
    ) -> None:
        if max_buffer_chars < 32:
            raise ValueError("max_buffer_chars must be at least 32")
        self.markdown = markdown
        self.urls = urls
        self._inline_only = inline
        self.max_buffer_chars = max_buffer_chars
        self.suppressed_constructs = 0
        self.max_buffered_chars = 0
        self._buffer = ""
        self._format_buffer = ""
        self._format_previous = ""
        self._format_depth = 0
        self._link_code = 0
        self._link_block = False
        self._block_list_indent: int | None = None
        self._format_fences: dict[int, tuple[int, bool]] = {}
        self._format_labels: dict[int, _RenderedLabel] = {}
        self._format_label_chars = 0
        self._format_block = False
        self._format_code = 0
        self._long_url_tail: str | None = None
        self._long_url_brackets = (0, 0)
        self._long_url_closer = ""
        self._long_url_phase: _BareUrlPhase = "authority"
        self._long_destination: _LongDestination | None = None
        self._strong_open = False
        self._line_start = True
        self._previous_line_list = False
        self._line_is_list = False
        self._needs_separator = False
        self._last_output = ""
        # A four-character raw suffix can lose the last real word behind
        # a whitespace run. Retain significant output independently.
        self._recent_nonspace = ""
        self._link_previous = ""
        self._link_backslashes = 0
        self._original_line_prefix = ""
        self._original_previous_prefix = ""
        self._original_previous_list = False
        self._original_prefix_complete = False
        self._original_replayed_chars = 0
        self._original_list_indent: int | None = None
        self._block_quote_prefix = ""
        self._inline_scope = _InlineScope(markdown)
        self._link_line_start = True
        self._pending_list_prefix = ""
        self._pending_list_link = False
        self._pending_list_newline = False
        self._format_backslashes = 0
        self._paired_open = {"__": False, "~~": False}
        self._triple_open = {"*": False, "_": False}
        # A removed link may separate a real Markdown line prefix or a
        # matching emphasis closer. Resolve only that boundary, not all prose.
        self._suppressed_line_break = False
        self._suppressed_closer: str | None = None
        self._suppressed_opener: str | None = None
        self._completed_links: tuple[dict[int, int], array, dict[int, int]] | None = (
            None
        )
        self._complete_mode = False
        self._completed_last_markers: dict[int, dict[str, int]] | None = None
        self._completed_singles: dict[str, array] | None = None
        self._completed_nested_singles: dict[str, array] | None = None

    @property
    def buffered_chars(self) -> int:
        """Return the current unresolved character count."""
        return (
            len(self._buffer)
            + len(self._format_buffer)
            + self._format_label_chars
            + len(self._long_url_tail or "")
        )

    def feed(self, delta: str) -> str:
        """Consume one arbitrary provider delta and return speech-safe text."""
        if not delta:
            return ""
        self._buffer += delta
        self.max_buffered_chars = max(self.max_buffered_chars, self.buffered_chars)
        link_safe = self._drain_links(final=False)
        result = self._format(link_safe, final=False)
        self.max_buffered_chars = max(self.max_buffered_chars, self.buffered_chars)
        return result

    def finish(self) -> str:
        """Resolve and return the remaining tail at end of stream."""
        link_safe = self._drain_links(final=True)
        if self._needs_separator:
            self._needs_separator = False
        return self._format(link_safe, final=True)

    def process_complete(self, text: str) -> str:
        """Clean a known-complete answer without speculative streaming overflow.

        Use the same recognizers and output rules as streaming, but tell both
        stages immediately that no further provider chunks can arrive.
        """
        if self._complete_mode or self._buffer or self._format_buffer:
            raise ValueError("Completed speech requires a fresh sanitizer")
        self._complete_mode = True
        if self.markdown and len(text) > DEFAULT_STREAMING_BUFFER_LIMIT and "[" in text:
            self._completed_links = self._index_complete_links(text)
        self._buffer = text
        link_safe = self._drain_links(final=True)
        if self._needs_separator:
            self._needs_separator = False
        return self._format(link_safe, final=True)

    def _emit(self, output: list[str], text: str) -> None:
        if self._pending_list_prefix:
            if "\n" in text and text.isspace():
                prefix = self._pending_list_prefix
                removed_line = self._pending_list_link and (
                    not self._last_output or self._last_output.endswith("\n")
                )
                preserve_number = (
                    not self._pending_list_link
                    and self._held_prefix_has_numeric_payload(prefix + text)
                )
                self._pending_list_prefix = ""
                self._needs_separator = False
                self._pending_list_link = False
                if preserve_number:
                    self._emit_plain(output, prefix)
                if removed_line:
                    return
            elif text.isspace():
                if not (
                    self._pending_list_link
                    and self._suppressed_opener
                    and self._pending_list_prefix.endswith(self._suppressed_opener)
                ):
                    self._pending_list_prefix += text[
                        : max(0, _FORMAT_PREFIX_LIMIT - len(self._pending_list_prefix))
                    ]
                return
            elif len(self._pending_list_prefix) + len(
                text
            ) <= _FORMAT_PREFIX_LIMIT and all(char in "*_~" for char in text):
                # An inline wrapper is not yet readable list content. Keep its
                # bounded opener with the prefix until text or removal proves it.
                self._pending_list_prefix += text
                if self._pending_list_link:
                    # This is a new, still-held delimiter after the removal,
                    # so the next citation must classify this opener afresh.
                    self._needs_separator = False
                    self._suppressed_opener = self._suppressed_closer = None
                return
            elif (
                self._pending_list_link
                and len(self._pending_list_prefix) + len(text) <= _FORMAT_PREFIX_LIMIT
                and all(char in _URL_TRAILING_PUNCTUATION for char in text)
            ):
                # Terminal citation punctuation is not readable item content.
                # Replay it if real text follows; otherwise retire it with the item.
                self._pending_list_prefix += text
                return
            elif not text.isspace():
                prefix = self._pending_list_prefix
                self._pending_list_prefix = ""
                if self._pending_list_link:
                    prefix = self._without_redundant_item_period(prefix)
                    self._needs_separator = False
                    if self._pending_list_newline and not self._last_output.endswith(
                        "\n"
                    ):
                        self._emit_plain(output, "\n")
                self._pending_list_link = False
                self._emit_plain(output, prefix)
        self._emit_plain(output, text)

    def _without_redundant_item_period(self, prefix: str) -> str:
        """Drop a held citation's standalone period after a numbered marker.

        Only the bounded prefix is inspected. A period touching a following
        digit is retained, so a readable quantity such as .5 is unchanged.
        """
        offset = 0
        while offset < len(prefix):
            action, count = self._line_prefix(prefix, True, offset)
            if action not in {"strip", "number"} or not count:
                break
            offset += count
            tail = prefix[offset:]
            if action == "number" and tail.strip() == "." and tail[-1:].isspace():
                return prefix[:offset]
        return prefix

    def _held_prefix_has_numeric_payload(self, text: str) -> bool:
        """Distinguish ambiguous numbers from empty speculative list markers.

        The held chain is bounded by _FORMAT_PREFIX_LIMIT. Include its newline
        so the existing recognizer can classify a terminal numerical fragment.
        Numerical fragments remain payload even after an established list item;
        an empty item alone does not justify dropping numerical information.
        """
        offset = 0
        while offset < len(text):
            action, count = self._line_prefix(
                text, True, offset, preceding_list_item=True
            )
            if action not in {"strip", "number"} or not count:
                return False
            if action == "number":
                return True
            offset += count
        return False

    def _emit_plain(self, output: list[str], text: str) -> None:
        if not text:
            return
        if self._needs_separator:
            if text.isspace():
                if "\n" in text:
                    self._suppressed_line_break = True
                self._inline_scope.feed(text)
                return
            adjacent_opener = self._suppressed_opener is not None
            self._needs_separator = False
            self._suppressed_line_break = False
            self._suppressed_closer = None
            self._suppressed_opener = None
            if (
                (self._recent_nonspace or self._last_output)
                and not self._last_output[-1:].isspace()
                and not adjacent_opener
                and not text[0].isspace()
                and text[0] not in ",.;:!?)]}"
                and text[0] not in _URL_CJK_PROSE_PUNCTUATION
                and not (
                    self._inline_scope.opened
                    and text.startswith(self._inline_scope.opened[-1])
                    and text[0] in _URL_QUOTE_CLOSERS
                )
            ):
                output.append(" ")
                self._inline_scope.feed(" ")
                self._last_output += " "
        self._inline_scope.feed("x" if isinstance(text, _RenderedLabel) else text)
        self._link_line_start = text.endswith("\n")
        output.append(text)
        self._last_output += text
        significant = "".join(char for char in text if not char.isspace())
        if significant:
            self._recent_nonspace = (self._recent_nonspace + significant)[-16:]
        if len(self._last_output) > 4:
            self._last_output = self._last_output[-4:]

    def _suppress(self, output: list[str], end: int) -> int:
        # A closing delimiter (such as the final ** in **Result**) must not
        # be treated as the opener of an empty wrapper around this citation.
        # Longer nested wrappers are tested before shorter suffixes.
        self._inline_scope.removed()
        if self._pending_list_prefix:
            self._pending_list_link = True
        # Consecutive removals share one unresolved boundary. The trimmed raw
        # suffix cannot reclassify its original closing delimiter as an opener.
        if not self._needs_separator:
            self._suppressed_opener = None
            self._suppressed_closer = None
        if self.markdown and not self._needs_separator:
            suffix = self._pending_list_prefix or self._last_output
            for opener, closer in _URL_MARKDOWN_WRAPPERS:
                if not suffix.endswith(opener):
                    continue
                if self._is_escaped(suffix, len(suffix) - len(opener)):
                    break
                preceding = suffix[-len(opener) - 1 : -len(opener)]
                if preceding and (preceding.isalnum() or preceding in ".,;:!?)]}"):
                    # A rejected closing ** must not turn into an opening *
                    # just because the shorter marker is also a suffix.
                    break
                self._suppressed_opener = opener
                self._suppressed_closer = closer
                break
        self._suppressed_line_break = False
        # A held list prefix has emitted no content on this line. Keep its
        # preceding line boundary so removing the item cannot duplicate it.
        while (
            not self._pending_list_prefix
            and output
            and output[-1]
            and output[-1][-1].isspace()
        ):
            output[-1] = output[-1].rstrip()
            if not output[-1]:
                output.pop()
        if not output and not self._pending_list_prefix:
            # The adjacent whitespace may still be waiting for an emphasis
            # closer in the next stage. Apply the same removal in either stage.
            self._format_buffer = self._format_buffer.rstrip()
        if not self._pending_list_prefix:
            self._last_output = self._last_output.rstrip()
        self._needs_separator = True
        self.suppressed_constructs += 1
        return end

    def _hold_preceding_whitespace(self, output: list[str]) -> str:
        """Hold emitted whitespace without rewinding consumed source characters."""
        if self._pending_list_prefix:
            # Its source prefix has already been consumed. Replaying an earlier
            # newline after that prefix would reverse their order across chunks.
            return ""
        held: list[str] = []
        while output and output[-1].isspace():
            held.append(output.pop())
        whitespace = "".join(reversed(held))
        if whitespace:
            self._last_output = self._last_output[: -len(whitespace)]
        return whitespace

    def _discard_suppressed_opener(self, output: list[str], marker: str) -> bool:
        """Remove an adjacent opener still pending in either parsing stage.

        A directly adjacent removed link is the only missing span content.
        Its delimiter has not been released: the formatter buffers an
        unfinished marker at the end of a streamed delta.
        """
        if self._pending_list_prefix.endswith(marker):
            # This wrapper has not reached either inline stage yet.
            self._pending_list_prefix = self._pending_list_prefix[: -len(marker)]
            return True
        tail = ""
        for part in reversed(output):
            if len(tail) >= len(marker):
                break
            tail = part[-(len(marker) - len(tail)) :] + tail
        if not (self._format_buffer[-len(marker) :] + tail).endswith(marker):
            return False
        remaining = len(marker)
        while remaining and output:
            count = min(remaining, len(output[-1]))
            output[-1] = output[-1][:-count]
            if not output[-1]:
                output.pop()
            remaining -= count
        if remaining:
            self._format_buffer = self._format_buffer[:-remaining]
        self._last_output = self._last_output[: -len(marker)]
        self._recent_nonspace = self._recent_nonspace[: -len(marker)]
        self._inline_scope.discard(marker)
        return True

    def _resolve_suppression_boundary(
        self, output: list[str], text: str, index: int, *, final: bool
    ) -> int | None:
        """Return None if a post-link prefix needs more input; else chars to skip.

        Ordinary citations still collapse to a space. Retain an original line
        boundary only when the next characters form a Markdown block prefix.
        """
        if not self._needs_separator or text[index].isspace():
            return 0
        closer = self._suppressed_closer
        if closer and text[index] == closer[0]:
            remaining = text[index:]
            if len(remaining) < len(closer) and not final:
                return None
            if remaining.startswith(closer) and self._discard_suppressed_opener(
                output, self._suppressed_opener or closer
            ):
                # Skip following whitespace until real text. The whitespace
                # preceding the opener may already have reached a listener.
                self._needs_separator = True
                self._suppressed_line_break = False
                self._suppressed_closer = None
                self._suppressed_opener = None
                return len(closer)
        if (
            self.markdown
            and self._suppressed_line_break
            and text[index] in "#>-+*0123456789"
        ):
            previous = self._original_previous_prefix.lstrip(" \t")
            # Numeric prefixes alone remain ambiguous. Preserve a boundary for
            # consecutive numbered items, without stripping standalone ordinals.
            before = re.match(r"([0-9]{1,9})[.)]\s", previous)
            after = re.match(
                r"([0-9]{1,9})[.)]\s", text[index : index + _FORMAT_PREFIX_LIMIT]
            )
            numbered = bool(
                before
                and after
                and (
                    int(after[1]) == int(before[1]) + 1 or before[1] == after[1] == "1"
                )
            )
            previous_action, _ = self._line_prefix(previous, True)
            previous_bullet = previous_action == "strip" and previous[:1] in {
                "-",
                "+",
                "*",
            }
            action, _ = self._line_prefix(
                text,
                final,
                index,
                preceding_list_item=self._original_previous_list
                or previous_bullet
                or numbered,
            )
            if not final and before and after is None and text[index].isdigit():
                prefix_action, _ = self._line_prefix(
                    text, final, index, preceding_list_item=True
                )
                if (
                    prefix_action == "incomplete"
                    and len(text) - index <= _FORMAT_PREFIX_LIMIT
                ):
                    return None
            if action == "incomplete" and not final:
                if len(text) - index <= _FORMAT_PREFIX_LIMIT:
                    return None
            elif action == "strip" or (
                action == "number"
                and (self._original_previous_list or previous_bullet or numbered)
            ):
                self._needs_separator = False
                self._suppressed_line_break = False
                self._suppressed_closer = None
                self._suppressed_opener = None
                if self._recent_nonspace and not self._last_output.endswith("\n"):
                    self._emit(output, "\n")
        return 0

    @staticmethod
    def _is_escaped(text: str, index: int, preceding_backslashes: int = 0) -> bool:
        """Whether a syntax character has an odd-length backslash prefix."""
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            cursor -= 1
        count = index - 1 - cursor
        if cursor < 0:
            count += preceding_backslashes
        return bool(count % 2)

    def _bare_url_token_end(
        self,
        text: str,
        start: int,
        phase: _BareUrlPhase,
        *,
        final: bool,
        closer: str = "",
    ) -> tuple[int, _BareUrlPhase, bool]:
        """Return the URL boundary and whether punctuation needs more input."""
        end = start
        while end < len(text):
            char = text[end]
            if char.isspace() or char in "<>":
                break
            if char in "?#":
                phase = "query"
            elif char == "/" and phase != "query":
                phase = "path"
            elif phase in ("authority", "long_userinfo") and char == "@":
                phase = "host"
            elif char in _URL_PROSE_DASHES or char in _URL_CJK_PROSE_PUNCTUATION:
                if phase == "host":
                    break
                closes_wrapper = self._wrapper_before_url_dash(
                    text, start, end, phase, closer, final=final
                )
                if closes_wrapper is None:
                    return end, phase, True
                if closes_wrapper:
                    break
                if (
                    phase == "path"
                    and not closer
                    and char in _URL_CJK_PROSE_PUNCTUATION
                ):
                    break
                if phase == "path" and char == "\u2014":
                    limit = min(len(text), end + _URL_PROSE_WORD_LIMIT + 2)
                    word_end = end + 1
                    while word_end < limit and not text[word_end].isspace():
                        word_end += 1
                    suffix = text[end + 1 : word_end]
                    word = suffix.rstrip(".,;:!?")
                    if (
                        word_end < limit
                        or (final and word_end == len(text) and word != suffix)
                    ) and _URL_PROSE_WORD.fullmatch(word):
                        break
                    if (
                        word_end == len(text) < end + _URL_PROSE_WORD_LIMIT + 2
                        and not final
                    ):
                        return end, phase, True
                if phase == "authority":
                    # A raw @ before the path ends userinfo. Until this can be
                    # checked, releasing the dash and suffix could expose it.
                    limit = min(len(text), end + self.max_buffer_chars + 1)
                    cursor = end + 1
                    while (
                        cursor < limit
                        and not text[cursor].isspace()
                        and text[cursor] not in "<>/?#@"
                    ):
                        cursor += 1
                    if cursor < limit:
                        if text[cursor] != "@":
                            break
                        phase = "host"
                        end = cursor
                    elif limit - end > self.max_buffer_chars:
                        # An exceptionally long ambiguous token must stay
                        # suppressed. Look for @ in later chunks without
                        # retaining the ambiguous suffix.
                        phase = "long_userinfo"
                        end = limit - 1
                    elif not final:
                        return end, phase, True
                    else:
                        break
            end += 1
        return end, phase, False

    def _wrapper_before_url_dash(
        self,
        text: str,
        start: int,
        dash: int,
        phase: _BareUrlPhase,
        closer: str,
        *,
        final: bool,
    ) -> bool | None:
        """Recognize a closer before prose, allowing for a later URL closer."""
        before = text[max(start, dash - _FORMAT_PREFIX_LIMIT) : dash]
        if len(before) < _FORMAT_PREFIX_LIMIT and self._long_url_tail:
            before = (self._long_url_tail + before)[-_FORMAT_PREFIX_LIMIT:]
        before = before[: self._bare_url_end(before, 0, len(before), (0, 0))]
        if closer:
            if not self._terminal_url_closer(before, len(before), closer):
                return False
        else:
            size = self._inline_scope.url_suffix(before)
            if not size:
                return False
            closer = before[-size:]
        if len(before) == len(closer):
            return False

        # A wrapper can also occur literally in a URL. If the longer token
        # has its own matching closer, keep suppressing through that closer.
        # Wait for enough input to tell, but keep overlong ambiguity hidden.
        limit = min(len(text), dash + self.max_buffer_chars + 1)
        end = dash + 1
        while end < limit and not text[end].isspace() and text[end] not in "<>":
            if text[end] in _URL_PROSE_DASHES and self._terminal_url_closer(
                text, end, closer
            ):
                break
            end += 1
        if end == limit:
            if limit - dash > self.max_buffer_chars:
                return False
            if not final:
                return None
        trailing = self._bare_url_end(text, dash + 1, end)
        if not self._terminal_url_closer(text, trailing, closer):
            return True
        # A matching opener immediately after the dash makes this a separate
        # prose span, even if its closer would also match the URL's wrapper.
        opener = _URL_OPENER_BY_CLOSER.get(closer)
        if (
            opener is None
            or dash + 1 + len(opener) >= trailing - len(closer)
            or not text.startswith(opener, dash + 1)
        ):
            return False
        # Before a confirmed host/path boundary these markers can still be
        # literal userinfo. A raw @ before /?# takes precedence there.
        return (
            phase not in ("authority", "long_userinfo")
            or _URL_USERINFO.match(text, dash + 1, trailing) is None
        )

    @staticmethod
    def _terminal_url_closer(text: str, end: int, closer: str) -> bool:
        return text.endswith(
            closer, 0, end
        ) and not StreamingSpeechSanitizer._is_escaped(text, end - len(closer))

    @staticmethod
    def _bare_url_end(
        text: str, start: int, end: int, brackets: tuple[int, int] | None = None
    ) -> int:
        """Leave sentence punctuation and excess closing brackets outside a URL.

        Count delimiters once so trimming a long URL remains linear. Balanced
        parentheses in a URL path (such as Wikipedia titles) stay part of it.
        """
        if brackets is None:
            candidate = text[start:end]
            brackets = (
                candidate.count(")") - candidate.count("("),
                candidate.count("]") - candidate.count("["),
            )
        parens, squares = brackets
        while end > start:
            char = text[end - 1]
            if char in _URL_TRAILING_PUNCTUATION:
                end -= 1
            elif char == ")" and parens > 0:
                parens -= 1
                end -= 1
            elif char == "]" and squares > 0:
                squares -= 1
                end -= 1
            else:
                break
        return end

    def _consume_long_url(self, candidate: str) -> None:
        """Keep only the suffix and delimiter counts of an overlong bare URL."""
        parens, squares = self._long_url_brackets
        self._long_url_brackets = (
            parens + candidate.count(")") - candidate.count("("),
            squares + candidate.count("]") - candidate.count("["),
        )
        self._long_url_tail = (
            (self._long_url_tail or "") + candidate[-_FORMAT_PREFIX_LIMIT:]
        )[-_FORMAT_PREFIX_LIMIT:]

    def _finish_long_url(self, output: list[str], end: int) -> None:
        tail = self._long_url_tail or ""
        trailing = self._bare_url_end(tail, 0, len(tail), self._long_url_brackets)
        boundary = self._bare_url_wrapper_end(tail, 0, trailing)
        self._suppress(output, end)
        if boundary < trailing and self._suppressed_closer is None:
            self._needs_separator = False
        # Let the ordinary suppression/formatting path handle trailing prose.
        index = boundary
        if self._long_url_closer and self._terminal_url_closer(
            tail, trailing, self._long_url_closer
        ):
            index = trailing
        while index < len(tail):
            skip = self._resolve_suppression_boundary(output, tail, index, final=True)
            if skip:
                index += skip
            else:
                self._emit(output, tail[index])
                index += 1
        self._long_url_tail = None
        self._long_url_brackets = (0, 0)
        self._long_url_closer = ""
        self._long_url_phase = "authority"

    @staticmethod
    def _is_url_start(text: str, index: int) -> bool:
        prefix = text[index : index + 8].casefold()
        return prefix.startswith(("http://", "https://")) or (
            prefix.startswith("www.") and _looks_like_www_address(text, index)
        )

    def _start_long_destination(
        self, output: list[str], text: str, index: int, *, markdown: bool
    ) -> int | None:
        """Commit to suppressing a recognized URL, keeping only delimiter state."""
        outer = markdown and text[index] == "("
        start = index + int(outer)
        image = markdown and text[start] == "!"
        start += int(image)
        label_end = (
            self._markdown_label_end(text, start, final=False) if markdown else -1
        )
        if markdown and (label_end < 0 or text[label_end + 1 : label_end + 2] != "("):
            return None
        position = label_end + 2 if markdown else start + 1
        if markdown:
            position = _link_whitespace_end(text, position, len(text))
            if position < 0:
                return None
        angle = text[position : position + 1] == "<" if markdown else text[start] == "<"
        url_start = position + int(markdown and angle)
        if not self._is_url_start(text, url_start):
            return None
        if (
            not markdown
            or image
            or not self._emit_link_label(
                output, text[start + 1 : label_end], text[url_start:]
            )
        ):
            self._suppress(output, url_start)
        self._long_destination = _LongDestination(
            ">" if angle else ")", markdown, outer
        )
        return url_start

    def _drain_long_destination(
        self, text: str, index: int, final: bool
    ) -> tuple[int, bool]:
        """Skip an overlong destination and let the regular title scanner finish it."""
        destination = self._long_destination
        assert destination is not None
        while destination.close in {">", ")"} and index < len(text):
            char = text[index]
            if destination.escaped:
                destination.escaped = False
            elif destination.markdown and char == "\\":
                destination.escaped = True
            elif char == "(" and destination.close == ")":
                destination.depth += 1
            elif char == destination.close and destination.depth:
                destination.depth -= 1
            elif char == destination.close:
                index += 1
                destination.close = (
                    "title" if destination.markdown and char == ">" else ""
                )
                break
            elif char in "<>\r\n" or (
                char.isspace()
                and (not destination.markdown or destination.close == ")")
            ):
                destination.close = (
                    "title" if destination.markdown and not destination.depth else ""
                )
                if not destination.close:
                    self._long_destination = None
                    return index, False
                break
            index += 1
        if destination.close == "title":
            prefix = "x"
            status, end = self._scan_markdown_destination(
                prefix + text[index:], 0, final
            )
            if status == "incomplete" and len(text) - index <= self.max_buffer_chars:
                return index, True
            if status != "complete":
                self._long_destination = None
                return index, False
            index += end - len(prefix)
            destination.close = ""
        if destination.close:
            return index, True
        if destination.outer_parenthesis:
            if index == len(text) and not final:
                return index, True
            if text[index : index + 1] == ")":
                index += 1
        self._long_destination = None
        return index, False

    def _track_original_lines(self, text: str) -> None:
        """Retain bounded original prefixes independently of formatting output."""
        if self._original_prefix_complete and "\n" not in text:
            return
        for char in text:
            if char == "\n":
                self._link_line_start = True
                if not self._link_code:
                    # Only authored list structure permits continuation fences
                    # deeper than three spaces. Code-looking lines inside a
                    # block must never establish new list context.
                    body = _QUOTE_PREFIX.sub("", self._original_line_prefix, count=1)
                    if marker := _LIST_PREFIX.match(body):
                        self._original_list_indent = marker.end()
                    elif body.strip() and (
                        self._original_list_indent is None
                        or len(body) - len(body.lstrip(" "))
                        < self._original_list_indent
                    ):
                        self._original_list_indent = None
                prefix = self._original_line_prefix.lstrip(" \t")
                action, _ = self._line_prefix(
                    prefix, True, preceding_list_item=self._original_previous_list
                )
                self._original_previous_list = bool(
                    (
                        action == "strip"
                        or (action == "number" and self._original_previous_list)
                    )
                    and prefix[:1] in "-+*0123456789"
                    and prefix
                )
                self._original_previous_prefix = self._original_line_prefix
                self._original_line_prefix = ""
                self._original_prefix_complete = False
            elif not self._original_prefix_complete:
                self._original_line_prefix += char
                if len(self._original_line_prefix) >= _FORMAT_PREFIX_LIMIT:
                    self._original_prefix_complete = True

    def _fence_container(self) -> tuple[bool, str, int | None]:
        """Recognize bounded original quote/list prefixes before a backtick run."""
        if self._original_prefix_complete:
            return False, "", None
        prefix = self._original_line_prefix
        quote = _QUOTE_PREFIX.match(prefix)
        quote_prefix = quote.group() if quote else ""
        body = prefix[len(quote_prefix) :]
        indent = self._original_list_indent
        # A fence can be the first content in a list item, including a nested
        # item. Consume markers only here, never in the fenced code body.
        consumed = 0
        while marker := _LIST_PREFIX.match(body):
            consumed += marker.end()
            body = body[marker.end() :]
            indent = consumed
        if body.strip(" "):
            return False, "", None
        spaces = len(body)
        allowed = spaces <= 3 or (indent is not None and indent <= spaces <= indent + 3)
        in_list = consumed or (indent is not None and spaces >= indent)
        return allowed, quote_prefix, indent if in_list else None

    def _quoted_code_prefix(self, text: str, index: int, final: bool) -> int | None:
        """Match only the opener's quote depth, retaining any code > operators.

        Quote spacing may vary between lines. None means a bounded prefix is
        split across chunks, zero means this line has no matching outer prefix.
        """
        start = index
        for _ in range(self._block_quote_prefix.count(">")):
            remaining = text[index : index + 4]
            if not final and (
                not remaining or (len(remaining) <= 3 and not remaining.strip(" "))
            ):
                return None
            marker = _QUOTE_MARKER.match(text, index)
            if marker is None:
                return 0
            index = marker.end()
            if index == len(text) and not final:
                return None
        return index - start

    def _block_container_prefix(self, text: str, index: int, final: bool) -> int | None:
        """Return quote width, -1 for a departed container, or None for a split prefix."""
        quote_width = 0
        if self._block_quote_prefix:
            quoted = self._quoted_code_prefix(text, index, final)
            if quoted is None:
                return None
            if not quoted:
                return -1
            quote_width = quoted
        indent = self._block_list_indent
        if indent is not None:
            cursor = index + quote_width
            columns = 0
            while cursor < len(text) and columns < indent and text[cursor] in " \t":
                columns += 1 if text[cursor] == " " else 4 - columns % 4
                cursor += 1
            if columns < indent:
                remainder = text[cursor : cursor + 1]
                if not remainder and not final:
                    return None
                # Blank lines can remain inside a list item without indentation.
                if remainder and remainder not in "\r\n":
                    return -1
        return quote_width

    def _end_block_fence(self) -> None:
        self._inline_scope.block_boundary(False)
        self._link_block = False
        self._link_code = 0
        self._block_quote_prefix = ""
        self._block_list_indent = None

    def _block_fence_can_close(self) -> bool:
        prefix = self._original_line_prefix
        if self._original_prefix_complete:
            return False
        quote = _QUOTE_PREFIX.match(prefix)
        quote_prefix = quote.group() if quote else ""
        body = prefix[len(quote_prefix) :]
        indent = self._block_list_indent or 0
        return (
            quote_prefix.count(">") == self._block_quote_prefix.count(">")
            and not body.strip(" ")
            and indent <= len(body) <= indent + 3
        )

    def _wrapped_bare_url(self, text: str, index: int, final: bool) -> tuple[str, int]:
        """Remove wrappers only with an adjacent URL and a matching terminal pair."""
        wrappers = (
            _URL_WRAPPERS_BY_INITIAL if self.markdown else _URL_QUOTES_BY_INITIAL
        ).get(text[index], ())
        for opener, closer in wrappers:
            tail = text[index : index + len(opener) + 8]
            if not text.startswith(opener, index):
                if not final and opener.startswith(tail):
                    return "incomplete", index
                continue
            start = index + len(opener)
            remaining = text[start : start + 8].lower()
            prefix = next(
                (item for item in _URL_PREFIXES if remaining.startswith(item)), None
            )
            if prefix is None:
                if not final and any(
                    item.startswith(remaining) for item in _URL_PREFIXES
                ):
                    return "incomplete", index
                continue
            end, phase, pending = self._bare_url_token_end(
                text,
                start + len(prefix),
                "host" if prefix == "www." else "authority",
                final=final,
                closer=closer,
            )
            if pending or (end == len(text) and not final):
                if len(text) - index > self.max_buffer_chars and self._is_url_start(
                    text, start
                ):
                    self._long_url_closer = closer
                    self._long_url_phase = phase
                    self._consume_long_url(text[start:end])
                    return "long", end
                return "incomplete", index
            end = self._bare_url_end(text, start, end)
            if self._terminal_url_closer(text, end, closer) and (
                prefix != "www."
                or _looks_like_www_address(text, start, end - len(closer))
            ):
                return "complete", end
        return "none", index

    def _bare_url_wrapper_end(self, text: str, start: int, end: int) -> int:
        """Use bounded lexical scope, never rescan already emitted prose."""
        return end - self._inline_scope.url_suffix(text[start:end])

    @classmethod
    def _has_closer(
        cls, text: str, start: int, limit: int, marker: str, preceding_backslashes: int
    ) -> bool:
        """Find an unescaped matching delimiter outside inline code."""
        cursor = start
        code = 0
        while cursor < limit:
            if text[cursor] == "`" and (
                code or not cls._is_escaped(text, cursor, preceding_backslashes)
            ):
                end = cursor
                while end < limit and text[end] == "`":
                    end += 1
                width = end - cursor
                if not code:
                    code = width
                elif width == code:
                    code = 0
                cursor = end
                continue
            if (
                not code
                and text.startswith(marker, cursor, limit)
                and not cls._is_escaped(text, cursor, preceding_backslashes)
            ):
                return True
            cursor += 1
        return False

    @staticmethod
    def _markdown_link_destination(raw: str) -> str:
        """Extract only the destination, excluding standard simple link titles."""
        raw = raw.lstrip(_LINK_WHITESPACE)
        angle = raw.startswith("<")
        start = position = int(angle)
        while position < len(raw):
            if (
                raw[position] == "\\"
                and position + 1 < len(raw)
                and raw[position + 1] in punctuation
            ):
                position += 2
                continue
            if raw[position] == ">" if angle else raw[position].isspace():
                break
            position += 1
        return raw[start:position]

    def _emit_link_label(self, output: list[str], label: str, destination: str) -> bool:
        """Render the label as inline content and omit address-only citations."""
        label = label.strip()
        if not label:
            return False
        inline = StreamingSpeechSanitizer(urls=self.urls, inline=True)
        readable = inline.process_complete(label)
        if not readable.strip():
            return False
        # The URL, not its optional quoted title, identifies matching-host
        # citation labels. Angle destinations are unwrapped for urlsplit.
        destination = self._markdown_link_destination(destination)
        spans = readable.spans if isinstance(readable, _SpeechText) else ()
        if (
            not spans
            and not self.urls
            and readable.casefold().startswith(_URL_PREFIXES)
            and not any(character.isspace() for character in readable)
        ):
            return False
        if not spans and _ADDRESS_LINK_LABEL.fullmatch(readable):
            try:
                host = urlsplit(
                    "//" + destination
                    if destination.casefold().startswith("www.")
                    else destination
                ).hostname
            except ValueError:
                host = None
            label_host = (
                readable.partition("/")[0].casefold().removeprefix("www.").rstrip(".")
            )
            if host and label_host == host.casefold().removeprefix("www.").rstrip("."):
                return False
            # Mismatched hostname-like labels are authored readable text too.
            # Preserve them rather than inferring meaning from file extensions.
        self._emit(output, _RenderedLabel(readable, spans))
        return True

    def _drain_links(self, *, final: bool) -> str:
        text = self._buffer
        output: list[str] = []
        fences: dict[int, bool] = {}
        index = 0
        held_whitespace = ""
        trailing_whitespace_start = len(text.rstrip())
        tracked = self._original_replayed_chars

        while index < len(text):
            self._track_original_lines(text[tracked:index])
            tracked = max(tracked, index)
            char = text[index]
            if self._long_destination is not None:
                index, waiting = self._drain_long_destination(text, index, final)
                if waiting:
                    break
                continue
            if self._long_url_tail is not None:
                end, self._long_url_phase, pending = self._bare_url_token_end(
                    text,
                    index,
                    self._long_url_phase,
                    final=final,
                    closer=self._long_url_closer,
                )
                self._consume_long_url(text[index:end])
                index = end
                if pending or end == len(text):
                    break
                self._finish_long_url(output, end)
                continue
            if (
                self._link_block
                and (self._block_quote_prefix or self._block_list_indent is not None)
                and not self._original_line_prefix
            ):
                prefix_length = self._block_container_prefix(text, index, final)
                if prefix_length is None:
                    break
                if prefix_length < 0:
                    # An implicit closer has no source characters. Notify the
                    # formatter without consuming the first line of prose.
                    fences[len(output)] = False
                    output.append("")
                    self._end_block_fence()
                    self._original_list_indent = None
                elif prefix_length:
                    # Remove the outer quote prefix only. A following > is code.
                    index += prefix_length
                    continue
            if (
                self.markdown
                and char == "`"
                and (
                    self._link_code
                    or not self._is_escaped(text, index, self._link_backslashes)
                )
            ):
                end = index
                while end < len(text) and text[end] == "`":
                    end += 1
                if end == len(text) and not final:
                    break
                count = end - index
                if self._link_block:
                    if count >= self._link_code and self._block_fence_can_close():
                        newline = text.find(
                            "\n", end, index + self.max_buffer_chars + 1
                        )
                        tail_end = newline if newline >= 0 else len(text)
                        if not text[end:tail_end].strip(" \t\r"):
                            if (
                                newline < 0
                                and not final
                                and tail_end - index <= self.max_buffer_chars
                            ):
                                break
                            if newline >= 0 or final:
                                self._emit(output, text[index:end])
                                fences[len(output) - 1] = False
                                self._end_block_fence()
                                index = tail_end
                                continue
                    self._emit(output, text[index:end])
                    index = end
                    continue
                # Inspect the original source before link removal/formatting can
                # manufacture a line-start fence. Only a bounded genuine block
                # opener may lose its info string; inline backticks stay literal.
                container, quote_prefix, list_indent = self._fence_container()
                if (
                    not self._inline_only
                    and not self._link_code
                    and count >= 3
                    and container
                ):
                    newline = text.find("\n", end, index + self.max_buffer_chars + 1)
                    info_end = newline if newline >= 0 else len(text)
                    if "`" not in text[end:info_end]:
                        if newline >= 0:
                            self._link_code = count
                            self._link_block = True
                            self._block_quote_prefix = quote_prefix
                            self._block_list_indent = list_indent
                            self._original_list_indent = list_indent
                            self._emit(output, text[index:end])
                            fences[len(output) - 1] = True
                            self._inline_scope.block_boundary(True)
                            index = newline
                            continue
                        if not final and len(text) - index <= self.max_buffer_chars:
                            break
                if not self._link_code:
                    self._link_code = count
                elif count == self._link_code:
                    self._link_code = 0
                    self._block_quote_prefix = ""
                self._emit(output, text[index:end])
                index = end
                continue
            if self._link_code:
                self._emit(output, char)
                index += 1
                continue

            # Recognize only bounded, top-level Setext underlines using authored
            # line context, before link removal can manufacture a heading.
            if (
                self.markdown
                and not self._inline_only
                and not self._original_line_prefix
            ):
                previous = self._original_previous_prefix
                title = previous.lstrip(" ")
                if (
                    title[:1].isalnum()
                    and len(previous) - len(title) <= 3
                    and not self._original_previous_list
                    and not any(marker in previous for marker in "\\`")
                    and self._line_prefix(previous, True)[0] == "none"
                ):
                    action, count = self._setext_equals_line(text, final, index)
                    if action == "incomplete":
                        break
                    if action == "strip":
                        index += count
                        continue

            if (
                self.markdown
                and not self._inline_only
                and (
                    self._link_line_start
                    or (self._pending_list_prefix and not self._pending_list_link)
                )
            ):
                action, count = self._line_prefix(
                    text, final, index, preceding_list_item=True
                )
                if (
                    action == "incomplete"
                    and not final
                    and len(text) - index <= _FORMAT_PREFIX_LIMIT
                ):
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                if (
                    action in {"strip", "number"}
                    and count + len(self._pending_list_prefix) <= _FORMAT_PREFIX_LIMIT
                ):
                    if not self._pending_list_prefix:
                        self._resolve_suppression_boundary(
                            output, text, index, final=final
                        )
                        self._pending_list_newline = (
                            bool(self._recent_nonspace) and not self._needs_separator
                        )
                    self._pending_list_prefix += text[index : index + count]
                    self._pending_list_link = False
                    self._link_line_start = False
                    index += count
                    continue
                self._link_line_start = False

            if (
                self.urls
                and char in "*_~\"'“\u2018"
                and not self._is_escaped(text, index, self._link_backslashes)
                and not (text[index - 1] if index else self._link_previous).isalnum()
            ):
                status, end = self._wrapped_bare_url(text, index, final)
                if status == "long":
                    self._hold_preceding_whitespace(output)
                    index = end
                    break
                if status == "incomplete":
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                if status == "complete":
                    index = self._suppress(output, end)
                    continue

            if self.markdown and not self._inline_only and char == "(":
                link_start = index + 1
                if link_start < len(text) and text[link_start] == "!":
                    link_start += 1
                if link_start < len(text) and text[link_start] == "[":
                    status, link_end = self._markdown_link_end(
                        text, link_start, final, self._completed_links
                    )
                    if status == "incomplete":
                        if len(text) - index >= self.max_buffer_chars:
                            # Resolve a long destination before its held
                            # whitespace runs out of buffer space.
                            start = self._start_long_destination(
                                output, text, index, markdown=True
                            )
                            if start is not None:
                                index = start
                                continue
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if status == "complete":
                        if link_end == len(text) and not final:
                            held_whitespace = self._hold_preceding_whitespace(output)
                            break
                        end = link_end + (
                            link_end < len(text) and text[link_end] == ")"
                        )
                        label_end = self._markdown_label_end(
                            text, link_start, self._completed_links
                        )
                        if text[index + 1] == "!" or not self._emit_link_label(
                            output,
                            text[link_start + 1 : label_end],
                            text[label_end + 2 : link_end - 1],
                        ):
                            self._suppress(output, end)
                        index = end
                        continue
                elif link_start >= len(text) and not final:
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break

            if (
                self.urls
                and char in "<("
                and not (
                    self.markdown
                    and self._is_escaped(text, index, self._link_backslashes)
                )
            ):
                # A URL wrapped by angle brackets or parentheses should not
                # leave empty delimiters in speech. Hold the opening delimiter
                # until a split URL prefix can be ruled out, or its closer
                # arrives; already-emitted speech cannot be taken back.
                remaining = text[index + 1 : index + 9].lower()
                wrapper_prefix = next(
                    (
                        prefix
                        for prefix in _URL_PREFIXES
                        if remaining.startswith(prefix)
                    ),
                    None,
                )
                if (
                    wrapper_prefix is None
                    and not final
                    and any(prefix.startswith(remaining) for prefix in _URL_PREFIXES)
                ):
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                if wrapper_prefix is not None:
                    closing = ">" if char == "<" else ")"
                    end = index + 1 + len(wrapper_prefix)
                    depth = 0
                    while end < len(text):
                        current = text[end]
                        if current.isspace() or current in "<>":
                            break
                        if char == "(" and current == "(":
                            depth += 1
                        elif current == closing:
                            if depth == 0:
                                break
                            depth -= 1
                        end += 1
                    if end == len(text) and not final:
                        if len(text) - index > self.max_buffer_chars:
                            start = self._start_long_destination(
                                output, text, index, markdown=False
                            )
                            if start is not None:
                                index = start
                                continue
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if (
                        end < len(text)
                        and text[end] == closing
                        and (
                            wrapper_prefix != "www."
                            or _looks_like_www_address(text, index + 1, end)
                        )
                    ):
                        index = self._suppress(output, end + 1)
                        continue

            markdown_start = index
            if (
                self.markdown
                and not self._inline_only
                and char == "!"
                and not self._is_escaped(text, index, self._link_backslashes)
            ):
                if index + 1 == len(text) and not final:
                    break
                if index + 1 < len(text) and text[index + 1] == "[":
                    markdown_start += 1
            if (
                self.markdown
                and not self._inline_only
                and text[markdown_start : markdown_start + 1] == "["
                and not self._is_escaped(text, markdown_start, self._link_backslashes)
            ):
                status, link_end = self._markdown_link_end(
                    text, markdown_start, final, self._completed_links
                )
                if status == "incomplete":
                    if len(text) - index >= self.max_buffer_chars:
                        start = self._start_long_destination(
                            output, text, index, markdown=True
                        )
                        if start is not None:
                            index = start
                            continue
                    if (
                        len(text) - index > self.max_buffer_chars
                        and self.urls
                        and self._is_url_start(text, markdown_start + 1)
                    ):
                        self._emit(output, text[index : markdown_start + 1])
                        index = markdown_start + 1
                        continue
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                if status == "complete":
                    label_end = self._markdown_label_end(
                        text, markdown_start, self._completed_links
                    )
                    if char == "!" or not self._emit_link_label(
                        output,
                        text[markdown_start + 1 : label_end],
                        text[label_end + 2 : link_end - 1],
                    ):
                        self._suppress(output, link_end)
                    index = link_end
                    continue

            if self.urls and char.lower() in "hw":
                # Inspect only the longest scheme, rather than repeatedly copying
                # and lowercasing the entire remaining answer.
                remaining = text[index : index + 8].lower()
                matching_prefix = next(
                    (
                        prefix
                        for prefix in _URL_PREFIXES
                        if remaining.startswith(prefix)
                    ),
                    None,
                )
                if (
                    matching_prefix is None
                    and not final
                    and any(prefix.startswith(remaining) for prefix in _URL_PREFIXES)
                ):
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                preceding = text[index - 1] if index else self._link_previous
                if matching_prefix is not None and not (
                    preceding.isalnum() or preceding == "@"
                ):
                    end, phase, pending = self._bare_url_token_end(
                        text,
                        index + len(matching_prefix),
                        "host" if matching_prefix == "www." else "authority",
                        final=final,
                    )
                    if pending or (end == len(text) and not final):
                        if len(text) - index > self.max_buffer_chars and (
                            matching_prefix != "www."
                            or _looks_like_www_address(text, index, end)
                        ):
                            self._hold_preceding_whitespace(output)
                            self._long_url_phase = phase
                            self._consume_long_url(text[index:end])
                            index = end
                            break
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if matching_prefix == "www." and not _looks_like_www_address(
                        text, index, end
                    ):
                        self._emit(output, char)
                        index += 1
                        continue
                    lexical_end = self._bare_url_end(text, index, end)
                    end = self._bare_url_wrapper_end(text, index, lexical_end)
                    index = self._suppress(output, end)
                    if end < lexical_end and self._suppressed_closer is None:
                        # A proved closer belongs beside its preceding prose,
                        # rather than after the separator used for plain links.
                        # An unresolved empty wrapper must still pass through
                        # boundary resolution to discard its pending opener.
                        self._needs_separator = False
                    continue

            # Keep a whitespace-only tail unresolved until meaningful input
            # arrives, so a citation cannot leak preceding blank lines solely
            # because the provider split them across multiple deltas.
            if char.isspace() and not final and index >= trailing_whitespace_start:
                break
            if char == "\n" and self._pending_list_prefix and self._pending_list_link:
                self._emit(output, char)
                index += 1
                continue
            skip = self._resolve_suppression_boundary(output, text, index, final=final)
            if skip is None:
                break
            if skip:
                index += skip
                continue
            self._emit(output, char)
            index += 1

        self._track_original_lines(text[tracked:index])
        if index:
            self._link_previous = text[index - 1]
            consumed = text[:index]
            trailing = len(consumed) - len(consumed.rstrip("\\"))
            self._link_backslashes = (
                self._link_backslashes + trailing
                if trailing == len(consumed)
                else trailing
            )
        # An otherwise bounded candidate must not overflow solely because
        # preceding whitespace was held with it.
        if not final and held_whitespace:
            whitespace_budget = self.max_buffer_chars - (len(text) - index)
            held_whitespace = (
                held_whitespace[-whitespace_budget:] if whitespace_budget > 0 else ""
            )
        self._buffer = held_whitespace + text[index:]
        # Whitespace held by the link stage has already been observed in the
        # source. Replaying it into the buffer must not create a second line.
        self._original_replayed_chars = len(held_whitespace)
        if not final and len(self._buffer) > self.max_buffer_chars:
            # A malformed construct or exceptionally long URL must not stall TTS.
            overflow = self._buffer
            trailing = len(overflow) - len(overflow.rstrip("\\"))
            # Even the literal overflow path must keep an unresolved escape
            # together with the next original source character.
            self._buffer = "\\" if trailing % 2 else ""
            if self._buffer:
                overflow = overflow[:-1]
            self._original_replayed_chars = 0
            self._emit(output, overflow)
            self._track_original_lines(overflow)
            self._link_previous = overflow[-1]
            self._link_backslashes = len(overflow) - len(overflow.rstrip("\\"))
            _LOGGER.debug(
                "Streaming speech sanitizer released malformed text at %d-char limit",
                self.max_buffer_chars,
            )
        elif final and self._buffer:
            self._emit(output, self._buffer)
            self._buffer = ""

        if final and self._long_url_tail is not None:
            self._finish_long_url(output, index)

        if final and self._pending_list_prefix:
            prefix = self._pending_list_prefix
            self._pending_list_prefix = ""
            if not self._pending_list_link:
                self._emit(output, prefix)
            self._pending_list_link = False
        offset = len(self._format_buffer)
        for part_index, part in enumerate(output):
            if isinstance(part, _RenderedLabel):
                self._format_labels[offset] = part
                self._format_label_chars += len(part) - 1
                # An inline atom must not contribute syntax to outer lookahead.
                output[part_index] = "x"
                offset += 1
                continue
            if part_index in fences:
                self._format_fences[offset] = len(part), fences[part_index]
            offset += len(part)
        return "".join(output)

    @staticmethod
    def _index_complete_links(
        text: str,
    ) -> tuple[dict[int, int], array, dict[int, int]]:
        """Index matching closures for long completed messages in a single pass."""
        invalid_url = array("I")
        open_parens = array("I")
        closed_parens: dict[int, int] = {}
        escaped = False
        for position, character in enumerate(text):
            if character == "\\" and not escaped:
                invalid_url.append(position)
                escaped = True
                continue
            if character.isspace() or character in "<>":
                invalid_url.append(position)
            if not escaped and character == "(":
                open_parens.append(position)
            elif not escaped and character == ")" and open_parens:
                opening = open_parens.pop()
                if opening and text[opening - 1] == "]":
                    closed_parens[opening] = position
            escaped = False
        # Many unmatched '[' can all point to the same ]( destination.
        # Validate each *distinct* complicated destination here, once, rather
        # than re-scanning a long title for every preceding opener. Keep the
        # same 3-field index contract and the same shared streaming validator.
        remaining_fallback_chars = max(16_384, len(text) * 4)
        remaining_validation_chars = max(16_384, len(text) * 4)
        for opening, closing in tuple(closed_parens.items()):
            url_start = opening + 1
            invalid_index = bisect_left(invalid_url, url_start)
            if (
                invalid_index >= len(invalid_url)
                or invalid_url[invalid_index] >= closing
            ):
                continue
            # Structural pairs can overlap deeply on malformed input. Bound
            # the primary validations too, not only their fallback scans.
            # Reserve a short independent scan per candidate so exhausting the
            # bulk budget cannot hide ordinary links later in the answer.
            window = min(closing - url_start + 1, remaining_validation_chars + 256)
            remaining_validation_chars -= max(0, window - 256)
            status, end = StreamingSpeechSanitizer._scan_markdown_destination(
                text, url_start, True, limit=url_start + window - 1
            )
            if status == "complete" and end == closing + 1:
                continue
            # A quoted title/angle destination may contain parentheses which
            # confuse the structural stack. Only these otherwise-rejected
            # candidates need a second scan; bound the aggregate malformed
            # input work instead of rescanning to EOF for each candidate.
            window = min(16_384, len(text) - url_start, remaining_fallback_chars)
            remaining_fallback_chars -= window
            if window <= 0:
                del closed_parens[opening]
                continue
            status, end = StreamingSpeechSanitizer._scan_markdown_destination(
                text, url_start, True, limit=url_start + window - 1
            )
            if status == "complete":
                closed_parens[opening] = end - 1
            else:
                del closed_parens[opening]

        # Such a link can also have no structural closing pair at all.
        # Probe only complex candidates that the old fast index could not
        # recognise. Bound those speculative probes so malformed bulk text
        # cannot turn complete-response cleanup into quadratic rescanning.
        probe_budget = max(16_384, len(text) * 4)
        remaining_short_probe_chars = remaining_extended_probe_chars = probe_budget
        whitespace_runs = _LINK_WHITESPACE_RUN.finditer(text)
        whitespace = next(whitespace_runs, None)
        next_closer = -1
        for candidate in re.finditer(r"\]\(", text):
            if remaining_short_probe_chars <= 0 and remaining_extended_probe_chars <= 0:
                break
            opening = candidate.end() - 1
            if opening in closed_parens:
                continue
            url_start = opening + 1
            if url_start >= len(text):
                continue
            # Walk candidate starts and whitespace runs in order. For a bare
            # destination the shared scanner checks for a title at its first
            # whitespace boundary, even if the destination is very long.
            while whitespace is not None and whitespace.end() <= url_start:
                whitespace = next(whitespace_runs, None)
            destination = url_start
            if whitespace is not None and whitespace.start() == url_start:
                if whitespace.lastgroup is None:
                    continue
                destination = whitespace.end()
                whitespace = next(whitespace_runs, None)
            if destination >= len(text):
                continue
            marker = destination
            if text[destination] != "<":
                if (
                    whitespace is None
                    or whitespace.lastgroup is None
                    or whitespace.end() >= len(text)
                ):
                    continue
                marker = whitespace.end()
                if text[marker] not in _LINK_TITLE_DELIMITERS:
                    continue
            # Reserve the original probe budget for angle openings and title
            # delimiters the legacy prefix could see. Newly discoverable ones
            # can overlap heavily and must not exhaust that budget.
            extended = marker - url_start >= _LEGACY_LINK_PROBE_PREFIX
            remaining = (
                remaining_extended_probe_chars
                if extended
                else remaining_short_probe_chars
            )
            window = min(16_384, len(text) - url_start, remaining)
            window_end = url_start + window
            if marker >= window_end:
                continue
            # Cache the next possible closer across overlapping windows.
            if next_closer < url_start:
                next_closer = text.find(")", url_start)
                if next_closer == -1:
                    next_closer = len(text)
            if next_closer >= window_end:
                continue
            status, end = StreamingSpeechSanitizer._scan_markdown_destination(
                text, url_start, True, limit=url_start + window - 1
            )
            if extended:
                remaining_extended_probe_chars -= window
            else:
                remaining_short_probe_chars -= window
            if status == "complete":
                closed_parens[opening] = end - 1
        brackets = dict(
            StreamingSpeechSanitizer._label_brackets(
                text, 0, final=True, destinations=closed_parens
            )
        )
        return brackets, invalid_url, closed_parens

    @staticmethod
    def _scan_markdown_destination(
        text: str, start: int, final: bool, *, limit: int | None = None
    ) -> tuple[str, int]:
        """Recognize simple Markdown URL + optional title with escaped delimiters.

        Streaming and indexed-complete parsing share this scanner. It only
        accepts links fully closed inside the current bounded input.
        """
        end = len(text) if limit is None else min(len(text), limit + 1)
        if start >= end:
            return ("no", start) if final else ("incomplete", start)

        def unfinished() -> tuple[str, int]:
            return ("no", start) if final else ("incomplete", start)

        position = _link_whitespace_end(text, start, end)
        if position < 0:
            return "no", start
        if position >= end:
            return unfinished()

        def escaped(at: int) -> bool:
            return text[at] == "\\" and at + 1 < end and text[at + 1] in punctuation

        if text[position] == "<":
            position += 1
            while position < end:
                char = text[position]
                if escaped(position):
                    position += 2
                    continue
                if char == ">":
                    position += 1
                    break
                if char in "<\r\n":
                    return "no", start
                position += 1
            else:
                return unfinished()
        else:
            depth = 0
            while position < end:
                char = text[position]
                if escaped(position):
                    position += 2
                    continue
                if char in "<>":
                    return "no", start
                if char.isspace():
                    if depth:
                        return "no", start
                    break
                if char == "(":
                    depth += 1
                elif char == ")":
                    if depth == 0:
                        # An empty Markdown destination, [label](), is valid.
                        return "complete", position + 1
                    depth -= 1
                position += 1
            if position == end:
                return unfinished()

        # Whitespace before the optional title is required by Markdown.
        whitespace_start = position
        position = _link_whitespace_end(text, position, end)
        if position < 0:
            return "no", start
        if position >= end:
            return unfinished()
        if text[position] == ")":
            return "complete", position + 1
        if position == whitespace_start or text[position] not in _LINK_TITLE_DELIMITERS:
            return "no", start
        closing = ")" if text[position] == "(" else text[position]
        position += 1
        while position < end:
            if escaped(position):
                position += 2
                continue
            if text[position] in "\r\n":
                position = _link_whitespace_end(text, position, end)
                if position < 0:
                    return "no", start
                continue
            if text[position] == closing:
                position += 1
                break
            position += 1
        else:
            return unfinished()
        position = _link_whitespace_end(text, position, end)
        if position < 0:
            return "no", start
        if position >= end:
            return unfinished()
        return ("complete", position + 1) if text[position] == ")" else ("no", start)

    @staticmethod
    def _label_brackets(
        text: str,
        start: int,
        *,
        final: bool,
        destinations: Mapping[int, int] | None = None,
    ) -> Iterator[tuple[int, int]]:
        """Pair structural brackets, treating matched code spans as atoms."""
        backticks: dict[int, list[int]] = {}
        if destinations is not None:
            for run in _BACKTICK_RUN.finditer(text, start):
                backticks.setdefault(run.end() - run.start(), []).append(run.start())
        openings: list[int] = []
        skip_to = start
        for token in _LABEL_SYNTAX.finditer(text, start):
            position = token.start()
            if position < skip_to:
                continue
            char = text[position]
            escaped = StreamingSpeechSanitizer._is_escaped(text, position)
            if char == "`":
                opener = position + int(escaped)
                width = token.end() - opener
                if not width:
                    continue
                if destinations is not None:
                    candidates = backticks.get(width, [])
                    following = bisect_right(candidates, opener)
                    closer = (
                        candidates[following] if following < len(candidates) else None
                    )
                else:
                    closer = next(
                        (
                            run.start()
                            for run in _BACKTICK_RUN.finditer(text, token.end())
                            if run.end() - run.start() == width
                        ),
                        None,
                    )
                if closer is not None:
                    skip_to = closer + width
                elif not final:
                    return
            elif not escaped:
                if char == "[":
                    openings.append(position)
                elif openings:
                    yield openings.pop(), position
                    if destinations is not None and position + 1 in destinations:
                        # A parsed link's destination is opaque. Its title
                        # cannot start a code span that consumes later prose.
                        skip_to = destinations[position + 1] + 1

    @staticmethod
    def _markdown_label_end(
        text: str,
        start: int,
        completed: tuple[dict[int, int], array, dict[int, int]] | None = None,
        *,
        final: bool = True,
    ) -> int:
        """Return the matching bracket for one Markdown label."""
        if completed is not None:
            return completed[0].get(start, -1)
        for opening, closing in StreamingSpeechSanitizer._label_brackets(
            text, start, final=final
        ):
            if opening == start:
                return closing
        return -1

    @staticmethod
    def _markdown_link_end(
        text: str,
        start: int,
        final: bool,
        completed: tuple[dict[int, int], array, dict[int, int]] | None = None,
    ) -> tuple[str, int]:
        if completed is not None:
            brackets, _, closed_parens = completed
            label_end = brackets.get(start, -1)
            if label_end < 0:
                return "no", start
            if label_end + 1 >= len(text) or text[label_end + 1] != "(":
                return "no", start
            url_start = label_end + 2
            if url_start >= len(text):
                return "no", start
            url_close = closed_parens.get(label_end + 1)
            if url_close is None:
                return "no", start
            # The completed index has already validated every complex
            # destination. The lookup remains constant-time for all '['.
            return "complete", url_close + 1

        label_end = StreamingSpeechSanitizer._markdown_label_end(
            text, start, final=final
        )
        if label_end == -1:
            return ("no", start) if final else ("incomplete", start)
        if label_end + 1 >= len(text):
            return ("no", start) if final else ("incomplete", start)
        if text[label_end + 1] != "(":
            return "no", start
        status, end = StreamingSpeechSanitizer._scan_markdown_destination(
            text, label_end + 2, final
        )
        return (status, end) if status == "complete" else (status, start)

    @classmethod
    def _index_complete_format(
        cls, text: str, fences: Mapping[int, tuple[int, bool]] | None = None
    ) -> tuple[dict[int, dict[str, int]], dict[str, array], dict[str, array]]:
        """Pre-index eligible delimiters once for complete-message lookahead.

        Index paired closers per prose segment so unmatched openers stay
        outside fenced code without repeatedly scanning a long tail. The
        arrays use one 32-bit position per possible single closer.
        """
        last_by_boundary: dict[int, dict[str, int]] = {}
        last: dict[str, int] = {}
        single = {"*": array("I"), "_": array("I")}
        nested_single = {"*": array("I"), "_": array("I")}
        code_width = 0
        block_code = False
        index = 0
        while index < len(text):
            if fences and index in fences:
                width, block_code = fences[index]
                if block_code:
                    last_by_boundary[index] = last
                    last = {}
                if width:
                    index += width
                    continue
            if block_code:
                index += 1
                continue
            char = text[index]
            if char == "`" and (code_width or not cls._is_escaped(text, index)):
                end = index + 1
                while end < len(text) and text[end] == "`":
                    end += 1
                width = end - index
                if not code_width:
                    code_width = width
                elif code_width == width:
                    code_width = 0
                index = end
                continue
            if not code_width and char in "*_~" and not cls._is_escaped(text, index):
                for marker in ("**", "__", "~~", "***", "___"):
                    if text.startswith(marker, index):
                        last[marker] = index
                if char in "*_" and index > 0 and not text[index - 1].isspace():
                    after = text[index + 1 : index + 2]
                    # A delimiter preceded by the same character cannot close
                    # single emphasis unless it is the last of an isolated
                    # three-character run. Preserve that nested-strong rule.
                    combined_close = (
                        index >= 2
                        and text[index - 2 : index] == char * 2
                        and (index < 3 or text[index - 3] != char)
                    )
                    if text[index - 1] != char or combined_close:
                        ordinary_close = not after or (
                            not after.isalnum() and after != char
                        )
                        if ordinary_close:
                            single[char].append(index)
                            nested_single[char].append(index)
                        elif after == char and text.startswith(char * 3, index):
                            # Only a currently open outer strong delimiter
                            # permits the first star of a nested *** closer.
                            nested_single[char].append(index)
            index += 1
        last_by_boundary[len(text)] = last
        return last_by_boundary, single, nested_single

    def _has_buffer_closer(self, start: int, limit: int, marker: str) -> bool:
        if self._completed_last_markers is not None:
            return self._completed_last_markers[limit].get(marker, -1) >= start
        return self._has_closer(
            self._format_buffer, start, limit, marker, self._format_backslashes
        )

    def _expand_label_atoms(
        self, text: str, *, source_offset: int = 0, strip_backticks: bool = False
    ) -> str:
        """Restore rendered labels when unresolved formatting falls back to text."""
        parts: list[str] = []
        start = 0
        for offset in range(len(text)) if self._format_labels else ():
            label = self._format_labels.get(offset + source_offset)
            if label is None:
                continue
            plain = text[start:offset]
            parts.extend((plain.replace("`", "") if strip_backticks else plain, label))
            start = offset + 1
        tail = text[start:]
        parts.append(tail.replace("`", "") if strip_backticks else tail)
        return _join_speech(parts, {})

    def _format(self, text: str, *, final: bool) -> str:
        if not self.markdown:
            return text
        self._format_buffer += text
        if (
            self._complete_mode
            and final
            and len(self._format_buffer) > DEFAULT_STREAMING_BUFFER_LIMIT
        ):
            (
                self._completed_last_markers,
                self._completed_singles,
                self._completed_nested_singles,
            ) = self._index_complete_format(self._format_buffer, self._format_fences)
        output: list[str] = []
        index = 0
        unresolved_emphasis = False
        literal_parts: dict[int, _LiteralKind] = {}
        buffer_end = len(self._format_buffer)
        # Fence offsets arrive in source order from the link-removal stage.
        fences = iter(self._format_fences)
        format_end = next(fences, buffer_end)

        while index < buffer_end:
            char = self._format_buffer[index]
            if (label := self._format_labels.get(index)) is not None:
                output.append(label)
                self._line_start = False
                index += 1
                continue
            if index == format_end:
                width, self._format_block = self._format_fences[index]
                format_end = next(fences, buffer_end)
                index += width
                if width:
                    self._line_start = False
                continue
            if self._format_block:
                literal_parts[len(output)] = "block_code"
                output.append(char)
                self._line_start = char == "\n"
                index += 1
                continue
            if char == "\\" and not self._format_code:
                following = self._format_buffer[index + 1 : index + 2]
                if not following and not final:
                    break
                if following and following in punctuation:
                    # Consume the original pair atomically. Its literal output
                    # must never be interpreted as a new Markdown delimiter.
                    if following in ",.;:!?":
                        literal_parts[len(output)] = "escaped"
                    output.append(following)
                    index += 2
                    self._line_start = False
                    continue
            if (
                not self._format_code
                and char in "`*_~"
                and self._is_escaped(
                    self._format_buffer, index, self._format_backslashes
                )
            ):
                output.append(char)
                index += 1
                self._line_start = False
                continue
            if char == "`":
                end = index
                while (
                    end < len(self._format_buffer) and self._format_buffer[end] == "`"
                ):
                    end += 1
                if end == len(self._format_buffer) and not final:
                    break
                count = end - index
                if not self._format_code:
                    self._format_code = count
                elif count == self._format_code:
                    self._format_code = 0
                else:
                    literal_parts[len(output)] = "code"
                    output.append(self._format_buffer[index:end])
                index = end
                self._line_start = False
                continue
            if self._format_code:
                literal_parts[len(output)] = "code"
                output.append(self._format_buffer[index])
                index += 1
                continue
            if self._line_start and not self._inline_only:
                rule_action, rule_count = self._horizontal_rule_line(
                    self._format_buffer, final, index
                )
                if rule_action == "incomplete":
                    break
                if rule_action == "strip":
                    index += rule_count
                    self._line_start = False
                    continue
                action, count = self._line_prefix(
                    self._format_buffer,
                    final,
                    index,
                    preceding_list_item=self._previous_line_list,
                )
                if action == "incomplete":
                    break
                if action in {"strip", "number"}:
                    prefix = self._format_buffer[index : index + count].lstrip(" \t")
                    self._line_is_list = self._line_is_list or bool(
                        prefix and prefix[0] in "-+*0123456789"
                    )
                    if action == "number":
                        following = self._format_buffer[
                            index + count : index + count + 1
                        ]
                        # Normalize only a marker with same-line content. Bare
                        # N) remains ambiguous, even after another list item.
                        if following and following not in "\r\n":
                            output.append(prefix.rstrip(" \t")[:-1] + ". ")
                        else:
                            output.append(prefix)
                    index += count
                    # Recognize nested block/list prefixes before inline text.
                    continue
                self._line_start = False

            char = self._format_buffer[index]
            if char == "\n":
                output.append(char)
                index += 1
                self._line_start = True
                self._previous_line_list = self._line_is_list
                self._line_is_list = False
                continue
            if char == "`":
                index += 1
                continue
            pair = self._format_buffer[index : index + 2]
            previous = (
                self._format_buffer[index - 1] if index else self._format_previous
            )
            if (
                char in "*_~"
                and not final
                and all(value == char for value in self._format_buffer[index:])
            ):
                # A delimiter run may continue in the next provider chunk.
                break
            if (
                char in "*_"
                and self._format_buffer.startswith(char * 3, index)
                and (
                    char != "_"
                    or not previous.isalnum()
                    or (
                        index + 3 == len(self._format_buffer)
                        or not self._format_buffer[index + 3].isalnum()
                    )
                )
            ):
                # An unmatched run is literal content (operators, identifiers,
                # or deliberately typed punctuation), not disposable styling.
                # An already-open strong span can legitimately end in ***.
                can_close = self._triple_open[char] or (
                    char == "*" and self._strong_open
                )
                if not can_close and not self._has_buffer_closer(
                    index + 3, format_end, char * 3
                ):
                    if not final and format_end == buffer_end:
                        unresolved_emphasis = True
                        break
                    output.append(char * 3)
                    index += 3
                    continue
                if self._triple_open[char]:
                    self._triple_open[char] = False
                elif char == "*" and self._strong_open:
                    self._strong_open = False
                else:
                    self._triple_open[char] = True
                index += 3
                continue
            if (
                char in "*_"
                and pair not in _FORMAT_MARKERS
                and not previous.isalnum()
                and previous != char
                and len(pair) == 2
                and not pair[1].isspace()
            ):
                # Buffer a possible emphasis span until its matching delimiter.
                # Word-internal underscores, arithmetic and unmatched markers
                # remain prose; the existing bounded buffer also applies here.
                end = -1
                code = 0
                if self._completed_singles is not None:
                    # Both candidate indexes are built in the same operation.
                    assert self._completed_nested_singles is not None
                    nested_outer_open = (
                        self._strong_open if char == "*" else self._paired_open["__"]
                    )
                    candidates = (
                        self._completed_nested_singles[char]
                        if nested_outer_open
                        else self._completed_singles[char]
                    )
                    first = bisect_right(candidates, index)
                    last = (
                        bisect_left(candidates, format_end, lo=first)
                        if format_end < buffer_end
                        else len(candidates)
                    )
                    tokens = ((candidates[pos], 0) for pos in range(first, last))
                else:
                    tokens = (
                        (
                            token.start(),
                            len(token[0]) if token[0].startswith("`") else 0,
                        )
                        for token in _EMPHASIS_TOKENS[char].finditer(
                            self._format_buffer, index + 1, format_end
                        )
                    )
                for candidate, code_marker in tokens:
                    if code_marker:
                        if not code:
                            code = code_marker
                        elif code_marker == code:
                            code = 0
                        continue
                    if code:
                        continue
                    end = candidate
                    after = self._format_buffer[end + 1 : end + 2]
                    combined_close = self._format_buffer[
                        max(index + 1, end - 2) : end
                    ] == char * 2 and (end < 3 or self._format_buffer[end - 3] != char)
                    closes_nested_outer = (
                        after == char
                        and self._format_buffer.startswith(char * 3, end)
                        and (
                            self._strong_open
                            if char == "*"
                            else self._paired_open["__"]
                        )
                    )
                    if (
                        self._is_escaped(
                            self._format_buffer, end, self._format_backslashes
                        )
                        or (self._format_buffer[end - 1] == char and not combined_close)
                        or (after == char and not closes_nested_outer)
                    ):
                        # A nested strong delimiter cannot close single emphasis.
                        end = -1
                        continue
                    if not self._format_buffer[end - 1].isspace() and (
                        not after
                        or (not after.isalnum() and after != char)
                        or closes_nested_outer
                    ):
                        break
                    end = -1
                if end < 0 or (end == len(self._format_buffer) - 1 and not final):
                    if not final and format_end == buffer_end:
                        unresolved_emphasis = True
                        break
                else:
                    body = self._format_buffer[index + 1 : end]
                    if self._format_depth < 32:
                        nested = StreamingSpeechSanitizer(urls=False)
                        nested._line_start = False
                        nested._format_depth = self._format_depth + 1
                        nested._strong_open = self._strong_open
                        nested._complete_mode = self._complete_mode
                        nested._format_labels = {
                            offset - index - 1: self._format_labels[offset]
                            for offset in range(index + 1, end)
                            if offset in self._format_labels
                        }
                        body = nested._format(body, final=True)
                        self._strong_open = nested._strong_open
                    elif self._format_labels:
                        body = self._expand_label_atoms(body, source_offset=index + 1)
                    output.append(body)
                    index = end + 1
                    continue
            if pair in _FORMAT_MARKERS:
                if (
                    pair == "**"
                    and not self._strong_open
                    and (previous.isalnum() or previous in (")", "]"))
                ):
                    after = self._format_buffer[index + 2 : index + 3]
                    if not after and not final:
                        break
                    if after.isdigit() or after in {"+", "-", "("}:
                        closer = _NUMERIC_EMPHASIS_COMPLETE.match(
                            self._format_buffer, index + 2
                        )
                        if not final and (
                            (
                                closer is None
                                and _NUMERIC_EMPHASIS_PREFIX.fullmatch(
                                    self._format_buffer[index + 2 :]
                                )
                                is not None
                            )
                            or (
                                closer is not None
                                and closer.end() == len(self._format_buffer)
                            )
                        ):
                            # Wait only while more input could complete or
                            # disambiguate the current numeric marker.
                            unresolved_emphasis = True
                            break
                        if closer is None:
                            # The suffix is now incompatible with the numeric
                            # grammar. Release this as a *literal* operator
                            # without re-entering the generic emphasis logic.
                            output.append(pair)
                            index += 2
                            continue
                if (
                    pair == "**"
                    and not self._strong_open
                    and (previous.isalnum() or previous in (")", "]"))
                    and self._format_buffer.find("**", index + 2, format_end) < 0
                ):
                    if not final and format_end == buffer_end:
                        unresolved_emphasis = True
                        break
                    output.append(pair)
                    index += 2
                    continue
                if (
                    previous.isspace()
                    and self._format_buffer[index + 2 : index + 3].isspace()
                ):
                    # Spaced operators (e.g. 2 ** 3) are literal mathematical text.
                    output.append(pair)
                    index += 2
                    continue
                if (
                    pair == "__"
                    and previous.isalnum()
                    and index + 2 == len(self._format_buffer)
                    and not final
                ):
                    # The next delta decides whether this is a word-internal
                    # literal or a closing emphasis delimiter.
                    break
                if pair != "__" or (
                    not previous.isalnum()
                    or index + 2 == len(self._format_buffer)
                    or not self._format_buffer[index + 2].isalnum()
                ):
                    already_open = (
                        self._strong_open if pair == "**" else self._paired_open[pair]
                    )
                    if not already_open and not self._has_buffer_closer(
                        index + 2, format_end, pair
                    ):
                        if not final and format_end == buffer_end:
                            unresolved_emphasis = True
                            break
                        output.append(pair)
                        index += 2
                        continue
                    if pair == "**":
                        self._strong_open = not self._strong_open
                    else:
                        self._paired_open[pair] = not self._paired_open[pair]
                    index += 2
                    continue
            if not final and index == len(self._format_buffer) - 1 and char in "*_~":
                break
            output.append(char)
            index += 1

        if index:
            self._format_previous = self._format_buffer[index - 1]
            consumed = self._format_buffer[:index]
            trailing = len(consumed) - len(consumed.rstrip("\\"))
            self._format_backslashes = (
                self._format_backslashes + trailing
                if trailing == len(consumed)
                else trailing
            )
        self._format_buffer = self._format_buffer[index:]
        if self._format_labels:
            self._format_labels = {
                position - index: label
                for position, label in self._format_labels.items()
                if position >= index
            }
            self._format_label_chars = sum(
                len(label) - 1 for label in self._format_labels.values()
            )
        if self._format_fences:
            self._format_fences = {
                position - index: marker
                for position, marker in self._format_fences.items()
                if position >= index
            }
        buffer_limit = (
            self.max_buffer_chars if unresolved_emphasis else _FORMAT_PREFIX_LIMIT
        )
        if (
            not final
            and len(self._format_buffer) + self._format_label_chars > buffer_limit
        ):
            overflow = self._format_buffer
            trailing = len(overflow) - len(overflow.rstrip("\\"))
            self._format_buffer = "\\" if trailing % 2 else ""
            self._format_fences.clear()
            if self._format_buffer:
                overflow = overflow[:-1]
            output.append(self._expand_label_atoms(overflow))
            self._format_labels.clear()
            self._format_label_chars = 0
            self._format_previous = overflow[-1]
            trailing = len(overflow) - len(overflow.rstrip("\\"))
            self._format_backslashes = (
                self._format_backslashes + trailing
                if trailing == len(overflow)
                else trailing
            )
            self._line_start = False
        elif final and self._format_buffer:
            output.append(
                self._expand_label_atoms(self._format_buffer, strip_backticks=True)
            )
            self._format_buffer = ""
            self._format_labels.clear()
            self._format_label_chars = 0
        return _join_speech(output, literal_parts)

    @staticmethod
    def _setext_equals_line(text: str, final: bool, start: int) -> tuple[str, int]:
        """Recognize a short contiguous equals underline, never a spaced rule."""
        end = text.find("\n", start, start + _FORMAT_PREFIX_LIMIT + 1)
        tail = text[start : end if end >= 0 else start + _FORMAT_PREFIX_LIMIT + 1]
        if end < 0 and not final and re.fullmatch(r" {0,3}", tail):
            return "incomplete", 0
        if (
            len(tail) > _FORMAT_PREFIX_LIMIT
            or re.fullmatch(r" {0,3}=+[ \t]*\r?", tail) is None
        ):
            return "none", 0
        if end < 0 and not final:
            return "incomplete", 0
        return "strip", len(tail)

    @staticmethod
    def _horizontal_rule_line(
        text: str, final: bool, start: int = 0
    ) -> tuple[str, int]:
        """Remove only standalone Markdown rules, not prose or escaped markers."""
        index = start
        while index < len(text) and text[index] in " \t" and index - start < 4:
            index += 1
        if index - start > 3 or index == len(text):
            return "none", 0
        marker = text[index]
        if marker not in "-*_":
            return "none", 0
        count = 0
        while index < len(text):
            char = text[index]
            if char == marker:
                count += 1
            elif char == "\n":
                return ("strip", index - start) if count >= 3 else ("none", 0)
            elif char not in " \t":
                return "none", 0
            index += 1
            if index - start >= _FORMAT_PREFIX_LIMIT:
                return "none", 0
        if final:
            return ("strip", index - start) if count >= 3 else ("none", 0)
        return "incomplete", 0

    @staticmethod
    def _line_prefix(
        text: str,
        final: bool,
        start: int = 0,
        *,
        preceding_list_item: bool = False,
    ) -> tuple[str, int]:
        whitespace = start
        while whitespace < len(text) and text[whitespace] in " \t":
            whitespace += 1
        if whitespace == len(text):
            return ("none", 0) if final else ("incomplete", 0)
        marker = text[whitespace]
        end = whitespace + 1

        if marker == "#":
            while end < len(text) and text[end] == "#" and end - whitespace < 6:
                end += 1
            if end == len(text):
                return ("none", 0) if final else ("incomplete", 0)
            if text[end].isspace():
                while end < len(text) and text[end] in " \t":
                    end += 1
                return "strip", end - start
        elif marker in ">-+*":
            if end == len(text):
                return ("none", 0) if final else ("incomplete", 0)
            if text[end].isspace():
                while end < len(text) and text[end] in " \t":
                    end += 1
                # "- 3 degrees" could be a negative quantity. Do not remove
                # its sign, even when the number arrives in a later delta.
                if marker == "-":
                    if end == len(text) and not final:
                        return "incomplete", 0
                    if end < len(text) and text[end].isdigit():
                        nested_action, nested_count = (
                            StreamingSpeechSanitizer._line_prefix(text, final, end)
                        )
                        if nested_action == "incomplete":
                            return "incomplete", 0
                        after = text[end + nested_count : end + nested_count + 1]
                        if nested_action != "number" or not after or after in "\r\n":
                            return "none", 0
                return "strip", end - start
        elif marker in "0123456789":
            while (
                end < len(text) and text[end] in "0123456789" and end - whitespace < 9
            ):
                end += 1
            if end < len(text) and text[end] in "0123456789":
                return "none", 0
            if end == len(text):
                return ("none", 0) if final else ("incomplete", 0)
            if text[end] in ".)":
                end += 1
                if end == len(text):
                    return ("none", 0) if final else ("incomplete", 0)
                if text[end].isspace():
                    while end < len(text) and text[end] in " \t":
                        end += 1
                    if end == len(text) and not final:
                        return "incomplete", 0
                    # Hold for citation-item removal, but retain numeric payload
                    # in the formatter rather than treating it as decoration.
                    return "number", end - start
        return "none", 0


def has_custom_speech_replacements(agent_config: Mapping[str, Any]) -> bool:
    """Return whether configured custom regex requires completed-response TTS."""
    if not agent_config.get(
        CONF_SPEECH_PROCESSING_ENABLED, DEFAULT_SPEECH_PROCESSING_ENABLED
    ):
        return False
    rules = agent_config.get(
        CONF_SPEECH_REGEX_REPLACEMENTS, DEFAULT_SPEECH_REGEX_REPLACEMENTS
    )
    return isinstance(rules, list) and bool(rules)


def needs_async_speech_cleanup(text: str, agent_config: Mapping[str, Any]) -> bool:
    """Keep substantial completed cleanup and custom regex off the HA loop."""
    return bool(
        agent_config.get(
            CONF_SPEECH_PROCESSING_ENABLED, DEFAULT_SPEECH_PROCESSING_ENABLED
        )
        and (
            len(text) >= COMPLETED_SPEECH_EXECUTOR_THRESHOLD
            or has_custom_speech_replacements(agent_config)
        )
    )


def streaming_speech_processing_enabled(agent_config: Mapping[str, Any]) -> bool:
    """Return whether built-in cleanup can safely run on progressive deltas."""
    return bool(
        agent_config.get(
            CONF_SPEECH_PROCESSING_ENABLED, DEFAULT_SPEECH_PROCESSING_ENABLED
        )
        and not has_custom_speech_replacements(agent_config)
        and (
            agent_config.get(CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN)
            or agent_config.get(CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS)
        )
    )


class _SpeechDeltaListener:
    """Sanitize only ChatLog listener deltas while ChatLog retains originals."""

    def __init__(
        self,
        listener: Callable[[Any, dict[str, Any]], None],
        agent_config: Mapping[str, Any],
        *,
        completed: bool = False,
    ) -> None:
        self._listener = listener
        self._agent_config = agent_config
        self._sanitizer = self._new_sanitizer()
        self._completed = completed
        self._completed_deltas: list[dict[str, Any]] = []

    def _new_sanitizer(self) -> StreamingSpeechSanitizer:
        return StreamingSpeechSanitizer(
            markdown=self._agent_config.get(
                CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN
            ),
            urls=self._agent_config.get(
                CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS
            ),
        )

    def __call__(self, chat_log: Any, delta: dict[str, Any]) -> None:
        if self._completed:
            # Completed provider responses are already available. Buffer only
            # the presentation deltas, never ChatLog's retained source content.
            self._completed_deltas.append(dict(delta))
            return
        if "role" in delta:
            self.flush(chat_log)
            self._sanitizer = self._new_sanitizer()
        content = delta.get("content")
        if not isinstance(content, str):
            self._listener(chat_log, delta)
            return
        safe = self._sanitizer.feed(content)
        filtered = dict(delta)
        if safe:
            filtered["content"] = safe
        else:
            filtered.pop("content", None)
        if filtered:
            self._listener(chat_log, filtered)

    async def async_flush_completed(self, hass: Any, chat_log: Any) -> None:
        """Emit known-complete messages, cleaning each in an executor.

        Provider adapters can split one *completed* message into multiple
        synthetic text deltas. Resolve their joint text once, preserving
        metadata and role boundaries without rewriting retained history.
        """
        if not self._completed or not self._completed_deltas:
            return
        pending = self._completed_deltas
        self._completed_deltas = []
        groups: list[list[dict[str, Any]]] = []
        group: list[dict[str, Any]] = []
        for delta in pending:
            if "role" in delta and group:
                groups.append(group)
                group = []
            group.append(delta)
        if group:
            groups.append(group)

        for group in groups:
            text = "".join(
                delta["content"]
                for delta in group
                if isinstance(delta.get("content"), str)
            )
            cleaned = (
                await hass.async_add_executor_job(
                    partial(
                        _built_in_cleanup,
                        text,
                        markdown=self._agent_config.get(
                            CONF_SPEECH_STRIP_MARKDOWN,
                            DEFAULT_SPEECH_STRIP_MARKDOWN,
                        ),
                        urls=self._agent_config.get(
                            CONF_SPEECH_STRIP_URLS,
                            DEFAULT_SPEECH_STRIP_URLS,
                        ),
                    )
                )
                if text
                else ""
            )
            emitted_content = False
            for delta in group:
                presented = dict(delta)
                if isinstance(delta.get("content"), str):
                    if emitted_content or not cleaned:
                        presented.pop("content", None)
                    else:
                        presented["content"] = cleaned
                    emitted_content = True
                if presented:
                    self._listener(chat_log, presented)

    def flush(self, chat_log: Any) -> None:
        """Forward the resolved end-of-message tail."""
        if self._completed:
            # A caller must await async_flush_completed before leaving the
            # completed provider path. On a failed parser, do not inject a
            # potentially enormous, partially interpreted response on-loop.
            self._completed_deltas.clear()
            return
        if tail := self._sanitizer.finish():
            self._listener(chat_log, {"content": tail})

    def log_summary(self) -> None:
        """Log non-sensitive speech diagnostics."""
        if self._completed:
            _LOGGER.debug("Completed-response speech cleanup applied")
            return
        _LOGGER.debug(
            "Streaming speech cleanup suppressed %d constructs; maximum buffered chars: %d",
            self._sanitizer.suppressed_constructs,
            self._sanitizer.max_buffered_chars,
        )


@contextmanager
def defer_assistant_presentation(chat_log: Any, agent_config: Mapping[str, Any]):
    """Let Assist synthesize the final reply when substitutions need all text."""
    listener = getattr(chat_log, "delta_listener", None)
    if listener is None or not has_custom_speech_replacements(agent_config):
        yield
        return

    role = "assistant"

    def guarded(log: Any, delta: dict[str, Any]) -> None:
        nonlocal role
        role = delta.get("role") or role
        if role == "assistant" and "content" in delta:
            delta = {key: value for key, value in delta.items() if key != "content"}
        if delta:
            listener(log, delta)

    chat_log.delta_listener = guarded
    try:
        yield
    finally:
        chat_log.delta_listener = listener


@contextmanager
def async_streaming_speech_cleanup(
    chat_log: Any,
    agent_config: Mapping[str, Any],
    *,
    completed: bool = False,
):
    """Present cleaned speech without altering ChatLog's original content."""
    listener = getattr(chat_log, "delta_listener", None)
    if listener is None or not streaming_speech_processing_enabled(agent_config):
        yield None
        return
    wrapper = _SpeechDeltaListener(listener, agent_config, completed=completed)
    chat_log.delta_listener = wrapper
    _LOGGER.debug("Streaming speech sanitizer enabled")
    try:
        yield wrapper
    finally:
        try:
            wrapper.flush(chat_log)
            wrapper.log_summary()
        finally:
            chat_log.delta_listener = listener


def _built_in_cleanup(text: str, *, markdown: bool, urls: bool) -> str:
    sanitizer = StreamingSpeechSanitizer(markdown=markdown, urls=urls)
    return sanitizer.process_complete(text)


def _final_whitespace_cleanup(text: str) -> str:
    parts: list[str] = []
    escaped: set[int] = set()
    start = 0
    for span in text.spans if isinstance(text, _SpeechText) else ():
        if span.kind == "escaped":
            escaped.update(range(span.start - start, span.end - start))
            continue
        left, right = span.start, span.end
        if span.kind == "block_code":
            # Fence delimiters leave surrounding blank lines and closer
            # indentation in the output. Keep those in prose spacing, while
            # retaining the indentation and whitespace of the code lines.
            body = text[left:right]
            left += body.rfind("\n", 0, len(body) - len(body.lstrip())) + 1
            newline = body.find("\n", len(body.rstrip()))
            if newline >= 0:
                right = span.start + newline
            if left >= right:
                continue
        prose = _normalize_prose_spacing(text[start:left], escaped)
        parts.append(prose.lstrip() if not parts else prose)
        parts.append(text[left:right])
        start = right
        escaped.clear()
    tail = _normalize_prose_spacing(text[start:], escaped)
    parts.append(tail.rstrip() if parts else tail.strip())
    return "".join(parts).strip("\r\n")


def _normalize_prose_spacing(text: str, escaped: set[int]) -> str:
    def punctuation_spacing(match: re.Match[str]) -> str:
        position = match.end() - 1
        punctuation = match.group(1)
        if position in escaped:
            return match.group()
        # A leading dot belongs to the following filename/identifier. Emoticons
        # similarly belong to the following token rather than the preceding word.
        if punctuation == "." and re.match(r"\w", text[position + 1 : position + 2]):
            return match.group()
        if punctuation in {":", ";"} and re.match(
            r"[:;]-?[()DPp/](?=\s|$)", text[position:]
        ):
            return match.group()
        return punctuation

    text = _SPACE_BEFORE_PUNCTUATION.sub(punctuation_spacing, text)
    text = _WHITESPACE.sub(" ", text)
    return _BLANK_LINES.sub("\n", text)


def process_speech_text(original_text: str, agent_config: Mapping[str, Any]) -> str:
    """Return TTS-only text using the deterministic completed-response pipeline."""
    if not agent_config.get(
        CONF_SPEECH_PROCESSING_ENABLED, DEFAULT_SPEECH_PROCESSING_ENABLED
    ):
        return original_text
    text = _built_in_cleanup(
        original_text,
        markdown=agent_config.get(
            CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN
        ),
        urls=agent_config.get(CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS),
    )
    from .regex_execution import apply_speech_replacements

    text = apply_speech_replacements(
        text,
        agent_config.get(
            CONF_SPEECH_REGEX_REPLACEMENTS, DEFAULT_SPEECH_REGEX_REPLACEMENTS
        ),
    )
    return _final_whitespace_cleanup(text)
