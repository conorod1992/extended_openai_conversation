"""Interpret provider text and reasoning records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RoutedReasoning:
    """Keep a provider reasoning item bound to the route that produced it."""

    route: str
    item: Any
    type = "reasoning"


def answer_text(content: Any) -> str:
    """Accept plain text and explicitly typed answer parts; ignore everything else."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        return "".join(answer_text(part) for part in content)
    kind = (
        content.get("type")
        if isinstance(content, Mapping)
        else getattr(content, "type", None)
    )
    if not isinstance(kind, str) or kind not in {"text", "output_text"}:
        return ""
    text = (
        content.get("text")
        if isinstance(content, Mapping)
        else getattr(content, "text", None)
    )
    return text if isinstance(text, str) else ""
