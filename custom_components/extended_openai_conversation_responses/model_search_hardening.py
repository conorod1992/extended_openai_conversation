"""Harden model-facing local search execution without changing retrieval semantics."""

from __future__ import annotations

from typing import Any


def _require_nonblank_query(arguments: dict[str, Any]) -> str:
    """Return a model-provided search query only when it contains useful text."""
    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("query is required")
    if not query.strip():
        raise ValueError("query must not be blank")
    return query
