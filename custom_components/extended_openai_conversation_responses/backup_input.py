"""Bound backup input before allocating Python containers or individual tokens."""

from __future__ import annotations

import codecs
from collections.abc import Iterator, Mapping, Set
from itertools import chain
import json
import re
import sys
from typing import Any

# Leave room for supported text-heavy backups while limiting the much larger
# cost of container-heavy input. Charges include array/dictionary allocation
# overhead and are conservative estimates, independent of the wire size.
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_TOKEN_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 100
_MAX_ATOM_BYTES = 8192
_CHUNK_SIZE = 64 * 1024
_CONTAINER_COST = 96
_MAPPING_ENTRY_COST = 96
_SLOT_COST = 16
_ATOM_COST = 48
_STRING_COST = 80

_STRING_END = re.compile(rb'["\\]')
_DELIMITER = re.compile(rb'["{}\[\],: \t\r\n]')
_WHITESPACE = re.compile(rb"[ \t\r\n]+")
_UTF8_BMP = re.compile(rb"[\xc4-\xef]")
_UTF8_ASTRAL = re.compile(rb"[\xf0-\xff]")
_UTF8_CONTINUATIONS = bytes(range(0x80, 0xC0))


class BackupInputLimit(ValueError):
    """Backup input exceeds a bounded resource or is not a tree."""


def _utf8_shape(span: bytes) -> tuple[int, int]:
    if span.isascii():
        return len(span), 1
    width = 4 if _UTF8_ASTRAL.search(span) else 2 if _UTF8_BMP.search(span) else 1
    # A continuation from the previous chunk was counted with its lead byte.
    return len(span.translate(None, _UTF8_CONTINUATIONS)), width


class JSONInputBudget:
    """Scan bounded chunks ahead of the JSON parser without retaining tokens.

    The actual parser checks syntax, numbers and Unicode. Here only character
    escapes, delimiters and token spans matter. A single string's cost accounts
    for the width required for its largest code point, as in a Python string.
    """

    def __init__(self) -> None:
        self.remaining = MAX_DECODED_BYTES
        self.depth = 0
        self._in_string = False
        self._escaped = False
        self._unicode_escape: bytearray | None = None
        self._token_bytes = 0
        self._text_length = 0
        self._text_width = 1

    def charge(self, size: int) -> None:
        self.remaining -= size
        if self.remaining < 0:
            raise BackupInputLimit("The backup exceeds the decoded data safety limit")

    def open_container(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise BackupInputLimit("The backup exceeds the nesting safety limit")
        self.charge(_CONTAINER_COST)

    def _measure_token(self, size: int, limit: int) -> None:
        self._token_bytes += size
        if self._token_bytes > limit:
            raise BackupInputLimit("The backup contains an oversized JSON token")

    def _text(self, length: int, width: int = 1) -> None:
        previous_size = self._text_length * self._text_width
        self._text_width = max(width, self._text_width)
        self._text_length += length
        size = self._text_length * self._text_width
        if size > MAX_TOKEN_BYTES:
            raise BackupInputLimit("The backup contains an oversized JSON token")
        self.charge(size - previous_size)

    def _text_span(self, span: bytes) -> None:
        self._measure_token(len(span), MAX_TOKEN_BYTES)
        self._text(*_utf8_shape(span))

    def _scan_string(self, data: bytes, offset: int) -> int:
        if self._unicode_escape is not None:
            end = min(len(data), offset + 4 - len(self._unicode_escape))
            self._measure_token(end - offset, MAX_TOKEN_BYTES)
            self._unicode_escape.extend(data[offset:end])
            if len(self._unicode_escape) == 4:
                try:
                    code_point = int(self._unicode_escape, 16)
                except ValueError:
                    code_point = 0  # The JSON parser reports the invalid escape.
                width = (
                    4
                    if 0xD800 <= code_point <= 0xDFFF
                    else 1
                    if code_point <= 0xFF
                    else 2
                )
                self._text(1, width)
                self._unicode_escape = None
            return end
        if self._escaped:
            self._escaped = False
            self._measure_token(1, MAX_TOKEN_BYTES)
            if data[offset] == ord("u"):
                self._unicode_escape = bytearray()
            else:
                self._text(1)
            return offset + 1
        match = _STRING_END.search(data, offset)
        end = match.start() if match else len(data)
        self._text_span(data[offset:end])
        if match is None:
            return end
        if data[end] == ord('"'):
            self._in_string = False
            self._token_bytes = 0
        else:
            self._measure_token(1, MAX_TOKEN_BYTES)
            self._escaped = True
        return end + 1

    def feed(self, data: bytes) -> None:
        """Reject over-budget regions before exposing them to a decoder."""
        if len(data) > _CHUNK_SIZE:
            for start in range(0, len(data), _CHUNK_SIZE):
                self._feed_chunk(data[start : start + _CHUNK_SIZE])
        else:
            self._feed_chunk(data)

    def _feed_chunk(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            if self._in_string:
                offset = self._scan_string(data, offset)
                continue
            token = data[offset]
            end = offset + 1
            if token == ord('"'):
                self._in_string = True
                self._text_length = 0
                self._text_width = 1
                self.charge(_STRING_COST)
            elif token in b"{[":
                self.open_container()
            elif token in b"}]":
                self.depth = max(0, self.depth - 1)
            elif token == ord(":"):
                self.charge(_MAPPING_ENTRY_COST)
            elif token == ord(","):
                self.charge(_SLOT_COST)
            elif token in b" \t\r\n":
                whitespace = _WHITESPACE.match(data, offset)
                assert whitespace is not None
                end = whitespace.end()
            else:
                delimiter = _DELIMITER.search(data, offset)
                end = delimiter.start() if delimiter else len(data)
                if not self._token_bytes:
                    self.charge(_ATOM_COST)
                self._measure_token(end - offset, _MAX_ATOM_BYTES)
                self.charge(end - offset)
                offset = end
                continue
            self._token_bytes = 0
            offset = end


def _decoded_json_chunks(value: bytes, encoding: str) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder(encoding)(errors="surrogatepass")
    for offset in range(0, len(value), _CHUNK_SIZE):
        yield decoder.decode(value[offset : offset + _CHUNK_SIZE])
    yield decoder.decode(b"", final=True)


def check_json_structure(value: str | bytes) -> None:
    """Preflight stdlib JSON input while preserving its historical encodings."""
    budget = JSONInputBudget()
    if isinstance(value, str):
        for offset in range(0, len(value), _CHUNK_SIZE):
            budget.feed(value[offset : offset + _CHUNK_SIZE].encode("utf-8"))
        return

    encoding = json.detect_encoding(value)
    if encoding in {"utf-8", "utf-8-sig"}:
        for offset in range(
            3 if encoding == "utf-8-sig" else 0, len(value), _CHUNK_SIZE
        ):
            budget.feed(value[offset : offset + _CHUNK_SIZE])
        return

    for decoded in _decoded_json_chunks(value, encoding):
        budget.feed(decoded.encode("utf-8", errors="surrogatepass"))


def bounded_legacy_json_text(value: bytes, max_text_bytes: int) -> str:
    """Preflight legacy JSON and bound the stdlib parser's whole-source string."""
    budget = JSONInputBudget()
    fragments = []
    total_length = 0
    maximum_width = 1
    for fragment in _decoded_json_chunks(value, json.detect_encoding(value)):
        encoded = fragment.encode("utf-8", errors="surrogatepass")
        total_length += len(fragment)
        maximum_width = max(maximum_width, _utf8_shape(encoded)[1])
        if total_length * maximum_width > max_text_bytes:
            raise BackupInputLimit(
                "The JSON backup source text exceeds the safety limit"
            )
        budget.feed(encoded)
        fragments.append(fragment)
    return "".join(fragments)


def check_decoded_structure(value: Any) -> None:
    """Bound an already decoded document before validation or deep copies."""
    budget = JSONInputBudget()
    stack: list[tuple[int, Iterator[Any]]] = [(0, iter((value,)))]
    active: set[int] = set()
    while stack:
        owner, values = stack[-1]
        for item in values:
            if isinstance(item, (Mapping, list, tuple, Set)):
                identity = id(item)
                if identity in active:
                    raise BackupInputLimit("The backup contains a circular reference")
                active.add(identity)
                budget.open_container()
                children: Iterator[Any]
                if isinstance(item, Mapping):
                    budget.charge(_MAPPING_ENTRY_COST * len(item))
                    children = chain(item, item.values())
                else:
                    budget.charge(_SLOT_COST * len(item))
                    children = iter(item)
                stack.append((identity, children))
                break
            size = sys.getsizeof(item)
            if isinstance(item, str):
                if size - sys.getsizeof("") > MAX_TOKEN_BYTES:
                    raise BackupInputLimit(
                        "The backup contains an oversized JSON token"
                    )
                budget.charge(max(_STRING_COST, size))
            else:
                if isinstance(item, int) and item.bit_length() > _MAX_ATOM_BYTES * 4:
                    raise BackupInputLimit(
                        "The backup contains an oversized JSON token"
                    )
                budget.charge(max(_ATOM_COST, size))
        else:
            active.discard(owner)
            budget.depth -= 1
            stack.pop()
