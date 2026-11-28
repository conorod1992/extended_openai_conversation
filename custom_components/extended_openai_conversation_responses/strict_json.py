"""Decode provider JSON using finite numbers and valid Unicode."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Set
from itertools import chain
import json
import math
import re
from typing import Any, NoReturn

_JSON_SURROGATE = re.compile(r"[\ud800-\udfff]|\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_SURROGATE_PAIR = re.compile(r"[\ud800-\udbff][\udc00-\udfff]")


def has_surrogate(value: str) -> bool:
    """Identify Python strings that cannot be passed to UTF-8 consumers."""
    return not value.isascii() and _SURROGATE.search(value) is not None


def replace_surrogates(value: str) -> str:
    """Make malformed UTF-8 code points visible without emitting them."""
    return _SURROGATE.sub(lambda match: f"\\u{ord(match[0]):04x}", value)


def join_surrogate_escapes(value: str) -> str:
    """Join correctly paired code units from a decoder that leaves them separate."""
    if not has_surrogate(value):
        return value
    joined = _SURROGATE_PAIR.sub(
        lambda match: chr(
            0x10000 + ((ord(match[0][0]) - 0xD800) << 10) + ord(match[0][1]) - 0xDC00
        ),
        value,
    )
    if has_surrogate(joined):
        raise ValueError("Document contains malformed Unicode")
    return joined


def check_document_unicode(document: Any) -> None:
    """Validate decoded keys and values with memory bounded by nesting depth."""
    stack: list[tuple[int, Iterator[Any]]] = [(0, iter((document,)))]
    active: set[int] = set()
    for_container = (Mapping, list, tuple, Set)
    while stack:
        owner, values = stack[-1]
        for value in values:
            if isinstance(value, str) and has_surrogate(value):
                raise ValueError("Document contains malformed Unicode")
            if isinstance(value, for_container):
                identity = id(value)
                if identity in active:
                    raise ValueError("Document contains a circular reference")
                active.add(identity)
                children = (
                    chain(value, value.values())
                    if isinstance(value, Mapping)
                    else iter(value)
                )
                stack.append((identity, children))
                break
        else:
            active.discard(owner)
            stack.pop()


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("Non-finite number in provider JSON")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_json_constant(value)
    return number


def strict_json_loads(text: str) -> Any:
    """Reject non-finite numbers and unpaired surrogates in decoded JSON."""
    data = json.loads(
        text, parse_constant=_reject_json_constant, parse_float=_finite_json_float
    )
    if _JSON_SURROGATE.search(text):
        # Validate after the decoder has joined valid pairs.
        check_document_unicode(data)
    return data
