"""Harden model-facing local search execution without changing retrieval semantics."""

from __future__ import annotations

from typing import Any

MAX_MEMORY_SEARCH_QUERY_CHARACTERS = 2048


def _require_nonblank_query(
    arguments: dict[str, Any], *, max_length: int | None = None
) -> str:
    """Return a model-provided search query only when it contains useful text."""
    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("query is required")
    if max_length is not None and len(query) > max_length:
        raise ValueError(f"query must be at most {max_length} characters")
    if not query.strip():
        raise ValueError("query must not be blank")
    return query
