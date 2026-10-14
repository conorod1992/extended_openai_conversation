"""Context-free JSON serialization with an explicit UTF-8 byte budget."""

from __future__ import annotations

from collections.abc import Iterator
import io
import json
from typing import Any

_TEXT_CHUNK_SIZE = 4096


class JSONBudgetExceeded(ValueError):
    """The serialized document would exceed its caller's byte budget."""


def check_utf8_budget(value: str, max_bytes: int) -> None:
    """Check text without allocating a second, potentially enormous byte string."""
    remaining = max_bytes
    for offset in range(0, len(value), _TEXT_CHUNK_SIZE):
        remaining -= len(value[offset : offset + _TEXT_CHUNK_SIZE].encode("utf-8"))
        if remaining < 0:
            raise JSONBudgetExceeded


def _check_string_budget(value: Any, max_bytes: int) -> None:
    """Reject oversized strings before JSONEncoder constructs a whole token.

    iterencode streams containers but emits each escaped string as one token.
    Count string literals (including keys) in bounded slices first. This is a
    lower bound on the document size; the encoder pass checks punctuation,
    numbers and formatting too. Shared containers count at every occurrence,
    while circular containers retain JSONEncoder's rejection semantics.
    """
    remaining = max_bytes
    active: set[int] = set()

    def visit(item: Any) -> None:
        nonlocal remaining
        if isinstance(item, str):
            remaining -= 2  # Opening and closing quotes.
            if remaining < 0:
                raise JSONBudgetExceeded
            for offset in range(0, len(item), _TEXT_CHUNK_SIZE):
                escaped = json.dumps(
                    item[offset : offset + _TEXT_CHUNK_SIZE], ensure_ascii=False
                )
                remaining -= len(escaped[1:-1].encode("utf-8"))
                if remaining < 0:
                    raise JSONBudgetExceeded
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError("Circular reference detected")
            active.add(identity)
            try:
                if isinstance(item, dict):
                    for key, child in item.items():
                        visit(key)
                        visit(child)
                else:
                    for child in item:
                        visit(child)
            finally:
                active.remove(identity)

    visit(value)


def iter_bounded_json(
    value: Any, max_bytes: int, *, indent: int | None = None
) -> Iterator[bytes]:
    """Emit standard JSON, refusing bytes beyond the budget before publication.

    The input is a detached snapshot and must not change during serialization.
    Scratch allocations for strings are bounded by the budget, including when
    one input scalar is much larger than the entire permitted document.
    Transport-specific limits and error messages belong to the caller.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("JSON byte budget must be a positive integer")
    _check_string_budget(value, max_bytes)
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )
    remaining = max_bytes
    for text in encoder.iterencode(value):
        encoded = text.encode("utf-8")
        remaining -= len(encoded)
        if remaining < 0:
            raise JSONBudgetExceeded
        yield encoded


def bounded_json_dumps(value: Any, max_bytes: int, *, indent: int = 2) -> str:
    """Collect a bounded one-message export without a whole-document byte copy."""
    with io.StringIO() as output:
        for chunk in iter_bounded_json(value, max_bytes, indent=indent):
            output.write(chunk.decode("utf-8"))
        return output.getvalue()
