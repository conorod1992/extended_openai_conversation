"""Decode provider JSON using finite numbers and valid Unicode."""

from __future__ import annotations

import json
import math
import re
from typing import Any, NoReturn

_JSON_SURROGATE = re.compile(r"[\ud800-\udfff]|\\u[dD][89a-fA-F][0-9a-fA-F]{2}")


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
        # Check the decoded strings and keys after the decoder has joined valid pairs.
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    return data
