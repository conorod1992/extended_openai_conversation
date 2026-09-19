"""Bound model-facing tool results."""

from __future__ import annotations

from typing import Any

MAX_MODEL_TOOL_RESULT_CHARACTERS = 32_000
_TOOL_RESULT_TRUNCATION_LABEL = "...[tool result truncated]"


def bounded_tool_result_text(value: Any) -> str:
    """Return a deterministic bounded representation for the next model request."""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= MAX_MODEL_TOOL_RESULT_CHARACTERS:
        return text
    suffix = f"\n{_TOOL_RESULT_TRUNCATION_LABEL} original_characters={len(text)}"
    return text[: max(0, MAX_MODEL_TOOL_RESULT_CHARACTERS - len(suffix))] + suffix
