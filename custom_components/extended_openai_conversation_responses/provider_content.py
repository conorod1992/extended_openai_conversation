"""Extract answer text without exposing provider reasoning or object representations."""

from collections.abc import Mapping, Sequence
from typing import Any


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
