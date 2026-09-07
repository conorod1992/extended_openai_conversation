"""Shared ceilings and pagination metadata for management result payloads."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import islice
from typing import Any

MANAGEMENT_USAGE_PAGE_MAX = 100
MANAGEMENT_DAILY_PAGE_MAX = 366
MANAGEMENT_ARCHIVE_LIST_PAGE_MAX = 100
MANAGEMENT_ARCHIVE_SEARCH_PAGE_MAX = 50
MANAGEMENT_ARCHIVE_TURN_PAGE_MAX = 20
MANAGEMENT_DEBUG_PROVIDER_PAGE_DEFAULT = 5
MANAGEMENT_DEBUG_PROVIDER_PAGE_MAX = 10
MANAGEMENT_DEBUG_TEXT_CHARACTERS = 64_000
MANAGEMENT_DEBUG_VALUE_CHARACTERS = 64_000
MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS = 8_000
MANAGEMENT_USAGE_BREAKDOWN_KEYS = 100


def page_from_iterable[T](
    items: Iterable[T], *, offset: int, limit: int
) -> tuple[list[T], bool]:
    """Read only one page plus one sentinel item from an iterable."""
    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    page_with_sentinel = list(
        islice(iter(items), safe_offset, safe_offset + safe_limit + 1)
    )
    return page_with_sentinel[:safe_limit], len(page_with_sentinel) > safe_limit


def page_metadata(
    *, offset: int, limit: int, returned: int, has_more: bool, total: int | None = None
) -> dict[str, Any]:
    """Return consistent pagination metadata without implying unknown completeness."""
    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    metadata: dict[str, Any] = {
        "offset": safe_offset,
        "limit": safe_limit,
        "returned": max(0, int(returned)),
        "has_more": bool(has_more),
        "next_offset": safe_offset + returned if has_more else None,
    }
    if total is not None:
        metadata["total"] = max(0, int(total))
    return metadata
