"""Complete bounded browsing for persistent-memory management surfaces."""

from __future__ import annotations

from typing import Any

from .memory import MAX_LIST_LIMIT, memory_as_dict, memory_revision


def _bounded_limit(message: dict[str, Any], default: int) -> int:
    """Return one management page size within the Memory backend's hard bound."""
    return max(1, min(int(message.get("limit", default)), MAX_LIST_LIMIT))


def _bounded_offset(message: dict[str, Any]) -> int:
    """Return a non-negative management page offset."""
    return max(0, int(message.get("offset", 0)))


async def _list_page(
    memory: Any,
    owner: str,
    scope_id: str,
    message: dict[str, Any],
    *,
    include_scope: bool,
) -> dict[str, Any]:
    """Return one exact Memory page plus an authoritative continuation flag."""
    limit = _bounded_limit(message, 100)
    offset = _bounded_offset(message)
    category = message.get("category")
    records = await memory.async_list(owner, category, limit, offset)
    has_more = False
    if len(records) == limit:
        has_more = bool(
            await memory.async_list(owner, category, 1, offset + len(records))
        )
    return {
        "memories": [
            management_memory_dict(record, include_scope=include_scope)
            for record in records
        ],
        "scope_id": scope_id,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


async def async_browse_memories(
    memory: Any,
    owner: str,
    scope_id: str,
    message: dict[str, Any],
    *,
    include_scope: bool,
) -> dict[str, Any]:
    """Return a complete, bounded list/search page for an authorized Memory owner."""
    query = str(message.get("query", "")).strip()
    if message.get("action") == "list" or not query:
        return await _list_page(
            memory, owner, scope_id, message, include_scope=include_scope
        )
    limit = _bounded_limit(message, 100)
    offset = _bounded_offset(message)
    records, total = await memory.async_browse(
        owner,
        query,
        limit=limit,
        offset=offset,
    )
    return {
        "memories": [
            management_memory_dict(record, include_scope=include_scope)
            for record in records
        ],
        "offset": offset,
        "limit": limit,
        "has_more": total > offset + len(records),
        "total": total,
        "query": query,
    }


def management_memory_dict(record: Any, *, include_scope: bool) -> dict[str, Any]:
    """Expose the edit token only on the Management projection."""
    return memory_as_dict(record, include_scope=include_scope) | {
        "revision": memory_revision(record)
    }
