"""Spoken-response post-processing that preserves the original assistant text."""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from functools import partial
import logging
import re
from typing import Any

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

_URL_PREFIXES = ("http://", "https://", "www.")
_URL_TRAILING_PUNCTUATION = frozenset(".,;:!?")
_WWW_ADDRESS = re.compile(
    r"www\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
    re.IGNORECASE,
)
_FORMAT_MARKERS = ("**", "__", "~~")
_EMPHASIS_TOKENS = {"*": re.compile(r"`+|\*"), "_": re.compile(r"`+|_")}
# A *viable prefix*, not merely a failed complete match, may suspend
# progressive speech. This includes split closing asterisks.
_NUMERIC_EMPHASIS_PREFIX = re.compile(r"[+-]?(?:\d+(?:\.\d*)?)?\*{0,2}\Z")
_NUMERIC_EMPHASIS_COMPLETE = re.compile(r"[+-]?\d+(?:\.\d+)?\*\*(?=$|[\s.,;:!?])")

# Suppress reference-only links but speak meaningful Markdown labels.
_CITATION_LINK_LABEL = re.compile(
    r"(?:\d+|sources?|references?|citations?|"
    r"(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s]*)?)",
    re.IGNORECASE,
)

DEFAULT_STREAMING_BUFFER_LIMIT = 4096
COMPLETED_SPEECH_EXECUTOR_THRESHOLD = 4096
_FORMAT_PREFIX_LIMIT = 32


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
    ) -> None:
        if max_buffer_chars < 32:
            raise ValueError("max_buffer_chars must be at least 32")
        self.markdown = markdown
        self.urls = urls
        self.max_buffer_chars = max_buffer_chars
        self.suppressed_constructs = 0
        self.max_buffered_chars = 0
        self._buffer = ""
        self._format_buffer = ""
        self._format_previous = ""
        self._format_depth = 0
        self._link_code = 0
        self._format_code = 0
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
        self._format_backslashes = 0
        self._paired_open = {"__": False, "~~": False}
        self._triple_open = {"*": False, "_": False}
        # A removed link may separate a real Markdown line prefix or a
        # matching emphasis closer. Resolve only that boundary, not all prose.
        self._suppressed_line_break = False
        self._suppressed_closer: str | None = None
        self._suppressed_opener: str | None = None
        self._completed_links: tuple[array, array, dict[int, int]] | None = None
        self._complete_mode = False
        self._completed_last_markers: dict[str, int] | None = None
        self._completed_singles: dict[str, array] | None = None
        self._completed_nested_singles: dict[str, array] | None = None

    @property
    def buffered_chars(self) -> int:
        """Return the current unresolved character count."""
        return len(self._buffer) + len(self._format_buffer)

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
        if not text:
            return
        if self._needs_separator:
            if text.isspace():
                if "\n" in text:
                    self._suppressed_line_break = True
                return
            self._needs_separator = False
            self._suppressed_line_break = False
            self._suppressed_closer = None
            self._suppressed_opener = None
            if (
                (self._recent_nonspace or self._last_output)
                and not self._last_output[-1:].isspace()
                and not text[0].isspace()
                and text[0] not in ",.;:!?)]}"
            ):
                output.append(" ")
                self._last_output += " "
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
        self._suppressed_opener = None
        self._suppressed_closer = None
        if self.markdown:
            wrappers = (
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
            for opener, closer in wrappers:
                if not self._last_output.endswith(opener):
                    continue
                preceding = self._last_output[-len(opener) - 1 : -len(opener)]
                if preceding and (preceding.isalnum() or preceding in ".,;:!?)]}"):
                    # A rejected closing ** must not turn into an opening *
                    # just because the shorter marker is also a suffix.
                    break
                self._suppressed_opener = opener
                self._suppressed_closer = closer
                break
        self._suppressed_line_break = False
        while output and output[-1] and output[-1][-1].isspace():
            output[-1] = output[-1].rstrip()
            if not output[-1]:
                output.pop()
        self._last_output = self._last_output.rstrip()
        self._needs_separator = True
        self.suppressed_constructs += 1
        return end

    def _hold_preceding_whitespace(self, output: list[str]) -> str:
        """Hold emitted whitespace without rewinding consumed source characters."""
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
                self._needs_separator = False
                self._suppressed_line_break = False
                self._suppressed_closer = None
                self._suppressed_opener = None
                return len(closer)
        if (
            self.markdown
            and self._suppressed_line_break
            and text[index] in "#>-+*0123456789"
        ):
            action, _ = self._line_prefix(
                text, final, index, preceding_list_item=self._previous_line_list
            )
            if action == "incomplete" and not final:
                if len(text) - index <= _FORMAT_PREFIX_LIMIT:
                    return None
            elif action == "strip":
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

    @staticmethod
    def _bare_url_end(text: str, start: int, end: int) -> int:
        """Leave sentence punctuation and excess closing brackets outside a URL.

        Count delimiters once so trimming a long URL remains linear. Balanced
        parentheses in a URL path (such as Wikipedia titles) stay part of it.
        """
        opens = {"(": 0, "[": 0}
        closes = {")": 0, "]": 0}
        for char in text[start:end]:
            if char in opens:
                opens[char] += 1
            elif char in closes:
                closes[char] += 1
        while end > start:
            char = text[end - 1]
            if char in _URL_TRAILING_PUNCTUATION:
                end -= 1
            elif char == ")" and closes[")"] > opens["("]:
                closes[")"] -= 1
                end -= 1
            elif char == "]" and closes["]"] > opens["["]:
                closes["]"] -= 1
                end -= 1
            else:
                break
        return end

    @classmethod
    def _has_closer(
        cls, text: str, start: int, marker: str, preceding_backslashes: int = 0
    ) -> bool:
        """Find an unescaped matching delimiter outside inline code."""
        cursor = start
        code = 0
        while cursor < len(text):
            if text[cursor] == "`" and (
                code or not cls._is_escaped(text, cursor, preceding_backslashes)
            ):
                end = cursor
                while end < len(text) and text[end] == "`":
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
                and text.startswith(marker, cursor)
                and not cls._is_escaped(text, cursor, preceding_backslashes)
            ):
                return True
            cursor += 1
        return False

    def _emit_link_label(self, output: list[str], label: str) -> bool:
        """Keep useful link labels; citation-like labels do not add spoken prose."""
        label = label.strip()
        if not label or _CITATION_LINK_LABEL.fullmatch(label):
            return False
        self._emit(output, label)
        return True

    def _drain_links(self, *, final: bool) -> str:
        text = self._buffer
        output: list[str] = []
        index = 0
        held_whitespace = ""
        trailing_whitespace_start = len(text.rstrip())

        while index < len(text):
            char = text[index]
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
                if not self._link_code:
                    self._link_code = count
                elif count == self._link_code:
                    self._link_code = 0
                self._emit(output, text[index:end])
                index = end
                continue
            if self._link_code:
                self._emit(output, char)
                index += 1
                continue

            if self.markdown and char == "(":
                link_start = index + 1
                if link_start < len(text) and text[link_start] == "!":
                    link_start += 1
                if link_start < len(text) and text[link_start] == "[":
                    status, link_end = self._markdown_link_end(
                        text, link_start, final, self._completed_links
                    )
                    if status == "incomplete":
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if status == "complete":
                        if link_end == len(text) and not final:
                            held_whitespace = self._hold_preceding_whitespace(output)
                            break
                        end = link_end + (
                            link_end < len(text) and text[link_end] == ")"
                        )
                        label_end = text.find("]", link_start + 1)
                        if text[link_start] == "!" or not self._emit_link_label(
                            output, text[link_start + 1 : label_end]
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
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if (
                        end < len(text)
                        and text[end] == closing
                        and (
                            wrapper_prefix != "www."
                            or _WWW_ADDRESS.match(text[index + 1 : end])
                        )
                    ):
                        index = self._suppress(output, end + 1)
                        continue

            markdown_start = index
            if self.markdown and char == "!":
                if index + 1 == len(text) and not final:
                    break
                if index + 1 < len(text) and text[index + 1] == "[":
                    markdown_start += 1
            if (
                self.markdown
                and text[markdown_start : markdown_start + 1] == "["
                and not self._is_escaped(text, markdown_start, self._link_backslashes)
            ):
                status, link_end = self._markdown_link_end(
                    text, markdown_start, final, self._completed_links
                )
                if status == "incomplete":
                    held_whitespace = self._hold_preceding_whitespace(output)
                    break
                if status == "complete":
                    label_end = text.find("]", markdown_start + 1)
                    if char == "!" or not self._emit_link_label(
                        output, text[markdown_start + 1 : label_end]
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
                    end = index + len(matching_prefix)
                    while (
                        end < len(text)
                        and not text[end].isspace()
                        and text[end] not in "<>"
                    ):
                        end += 1
                    if end == len(text) and not final:
                        held_whitespace = self._hold_preceding_whitespace(output)
                        break
                    if matching_prefix == "www." and not _WWW_ADDRESS.match(
                        text[index:end]
                    ):
                        self._emit(output, char)
                        index += 1
                        continue
                    index = self._suppress(output, self._bare_url_end(text, index, end))
                    continue

            # Keep a whitespace-only tail unresolved until meaningful input
            # arrives, so a citation cannot leak preceding blank lines solely
            # because the provider split them across multiple deltas.
            if char.isspace() and not final and index >= trailing_whitespace_start:
                break
            skip = self._resolve_suppression_boundary(output, text, index, final=final)
            if skip is None:
                break
            if skip:
                index += skip
                continue
            self._emit(output, char)
            index += 1

        if index:
            self._link_previous = text[index - 1]
            consumed = text[:index]
            trailing = len(consumed) - len(consumed.rstrip("\\"))
            self._link_backslashes = (
                self._link_backslashes + trailing
                if trailing == len(consumed)
                else trailing
            )
        self._buffer = held_whitespace + text[index:]
        if not final and len(self._buffer) > self.max_buffer_chars:
            # A malformed construct or exceptionally long URL must not stall TTS.
            overflow = self._buffer
            self._buffer = ""
            self._emit(output, overflow)
            self._link_previous = overflow[-1]
            _LOGGER.debug(
                "Streaming speech sanitizer released malformed text at %d-char limit",
                self.max_buffer_chars,
            )
        elif final and self._buffer:
            self._emit(output, self._buffer)
            self._buffer = ""

        return "".join(output)

    @staticmethod
    def _index_complete_links(text: str) -> tuple[array, array, dict[int, int]]:
        """Index only source positions needed for bounded completed link scans."""
        brackets = array("I")
        invalid_url = array("I")
        open_parens = array("I")
        closed_parens: dict[int, int] = {}
        for position, character in enumerate(text):
            if character == "]":
                brackets.append(position)
            if character.isspace() or character in "<>":
                invalid_url.append(position)
            if character == "(":
                open_parens.append(position)
            elif character == ")" and open_parens:
                opening = open_parens.pop()
                # Only ]( can introduce a Markdown URL in this recognizer.
                if opening and text[opening - 1] == "]":
                    closed_parens[opening] = position
        return brackets, invalid_url, closed_parens

    @staticmethod
    def _markdown_link_end(
        text: str,
        start: int,
        final: bool,
        completed: tuple[array, array, dict[int, int]] | None = None,
    ) -> tuple[str, int]:
        if completed is not None:
            brackets, invalid_url, closed_parens = completed
            position = bisect_right(brackets, start)
            if position >= len(brackets):
                return "no", start
            label_end = brackets[position]
            if label_end + 1 >= len(text) or text[label_end + 1] != "(":
                return "no", start
            url_start = label_end + 2
            if url_start >= len(text):
                return "no", start
            url_close = closed_parens.get(label_end + 1)
            if url_close is None:
                return "no", start
            next_invalid = bisect_left(invalid_url, url_start)
            if (
                next_invalid < len(invalid_url)
                and invalid_url[next_invalid] < url_close
            ):
                return "no", start
            return "complete", url_close + 1

        label_end = text.find("]", start + 1)
        if label_end == -1:
            return ("no", start) if final else ("incomplete", start)
        if label_end + 1 >= len(text):
            return ("no", start) if final else ("incomplete", start)
        if text[label_end + 1] != "(":
            return "no", start

        url_start = label_end + 2
        if url_start >= len(text):
            return ("no", start) if final else ("incomplete", start)
        depth = 0
        for index in range(url_start, len(text)):
            char = text[index]
            if char.isspace() or char in "<>\n":
                return "no", start
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    return "complete", index + 1
                depth -= 1
        return ("no", start) if final else ("incomplete", start)

    @classmethod
    def _index_complete_format(
        cls, text: str
    ) -> tuple[dict[str, int], dict[str, array], dict[str, array]]:
        """Pre-index eligible delimiters once for complete-message lookahead.

        Unmatched openers must not rescan the same long tail repeatedly. The
        compact arrays use one 32-bit position per possible single closer.
        """
        last: dict[str, int] = {}
        single = {"*": array("I"), "_": array("I")}
        nested_single = {"*": array("I"), "_": array("I")}
        code_width = 0
        index = 0
        while index < len(text):
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
        return last, single, nested_single

    def _has_buffer_closer(self, start: int, marker: str) -> bool:
        if self._completed_last_markers is not None:
            return self._completed_last_markers.get(marker, -1) >= start
        return self._has_closer(
            self._format_buffer, start, marker, self._format_backslashes
        )

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
            ) = self._index_complete_format(self._format_buffer)
        output: list[str] = []
        index = 0
        unresolved_emphasis = False

        while index < len(self._format_buffer):
            char = self._format_buffer[index]
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
                    output.append(self._format_buffer[index:end])
                index = end
                self._line_start = False
                continue
            if self._format_code:
                output.append(self._format_buffer[index])
                index += 1
                continue
            if self._line_start:
                action, count = self._line_prefix(
                    self._format_buffer,
                    final,
                    index,
                    preceding_list_item=self._previous_line_list,
                )
                if action == "incomplete":
                    break
                if action == "strip":
                    prefix = self._format_buffer[index:].lstrip(" \t")
                    self._line_is_list = bool(
                        prefix and prefix[0] in "-+*0123456789"
                    )
                    index += count
                    self._line_start = False
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
                if not can_close and not self._has_buffer_closer(index + 3, char * 3):
                    if not final:
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
                    tokens = (
                        (candidates[pos], 0) for pos in range(first, len(candidates))
                    )
                else:
                    tokens = (
                        (
                            token.start(),
                            len(token[0]) if token[0].startswith("`") else 0,
                        )
                        for token in _EMPHASIS_TOKENS[char].finditer(
                            self._format_buffer, index + 1
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
                    if not final:
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
                        body = nested._format(body, final=True)
                        self._strong_open = nested._strong_open
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
                    and self._format_buffer.find("**", index + 2) < 0
                ):
                    if not final:
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
                        index + 2, pair
                    ):
                        if not final:
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
        buffer_limit = (
            self.max_buffer_chars if unresolved_emphasis else _FORMAT_PREFIX_LIMIT
        )
        if not final and len(self._format_buffer) > buffer_limit:
            output.append(self._format_buffer)
            self._format_previous = self._format_buffer[-1]
            overflow = self._format_buffer
            trailing = len(overflow) - len(overflow.rstrip("\\"))
            self._format_backslashes = (
                self._format_backslashes + trailing
                if trailing == len(overflow)
                else trailing
            )
            self._format_buffer = ""
            self._line_start = False
        elif final and self._format_buffer:
            output.append(self._format_buffer.replace("`", ""))
            self._format_buffer = ""
        return "".join(output)

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
                    # A standalone "N." can be an ordinal date or a quantity.
                    # Suppress it only when continuing an already marked list.
                    if not preceding_list_item:
                        return "none", 0
                    while end < len(text) and text[end] in " \t":
                        end += 1
                    return "strip", end - start
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
    # Parentheses can be literal speech content (including a code-span call()).
    # Link suppression already removes the enclosing citation parentheses.
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n", text)
    return text.strip()


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
